"""
OCR submission and retrieval (§4.4, bottlenecks B2 and B8).

Two fixes live here.

**B2 — no polling loop.** The source documents specified *"poll
GetDocumentAnalysis, accumulating Blocks across NextToken pages"*, which blocks a
worker process in a sleep-poll for minutes per document and throttles under
concurrency. Instead ``NotificationChannel`` is passed to Textract, completion
arrives on SNS → SQS, and the worker submits and moves on.

**B8 — idempotent submission.** SQS is at-least-once and a slow job *will* be
redelivered inside the 15-minute visibility window. Nothing in the original design
prevented a second ``StartDocumentAnalysis``; a 3,000-page document re-submitted
three times costs $135 instead of $45. Two guards: a ``ClientRequestToken`` derived
from the idempotency key, and ``pipeline_jobs.external_job_id`` written **before
the call is considered complete**, so a redelivery resumes rather than re-submits.
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from shared.config import get_settings
from shared.enums import OCRRoute

log = logging.getLogger("cbc.ocr")

#: Textract's ClientRequestToken accepts [a-zA-Z0-9-_] up to 64 characters.
TOKEN_MAX = 64

#: Textract async limits per document (§4.6, C16). Triage submits a routed-page
#: subset rather than the whole plan set, so these are a guard against a document
#: that is pathological even after triage — not a workflow to plan around.
MAX_PAGES = 3000
MAX_BYTES = 500 * 1024 * 1024


class OCRError(RuntimeError):
    """Textract refused the request or returned a failed job."""


class DocumentTooLarge(OCRError):
    """Even the routed subset exceeds what Textract async accepts."""


def assert_within_limits(*, pages: int, size_bytes: int) -> None:
    """
    Refuse before spending, with a reason an estimator can act on.

    Both source documents quoted the 3,000-page / 500 MB limit and neither
    handled it; a document that silently fails at the API is a worse outcome than
    a slow one (§4.6).
    """
    if pages > MAX_PAGES:
        raise DocumentTooLarge(
            f"{pages} pages were routed to OCR, over Textract's {MAX_PAGES}-page limit. "
            f"Nothing has been spent. Split the bid set into separate uploads, or narrow "
            f"the routing table if this many pages were classified as schedules by mistake."
        )
    if size_bytes > MAX_BYTES:
        raise DocumentTooLarge(
            f"the routed subset is {size_bytes / 1024 / 1024:.0f} MB, over Textract's "
            f"{MAX_BYTES // 1024 // 1024} MB limit. Nothing has been spent. Split the bid "
            f"set into separate uploads."
        )


@dataclass(frozen=True)
class OCRSubmission:
    job_id: str
    route: OCRRoute
    feature_types: tuple[str, ...]
    already_running: bool = False


def _client():
    return boto3.client("textract", **get_settings().boto_kwargs_for("textract"))


def feature_types_for(route: OCRRoute) -> tuple[str, ...]:
    """
    Features to request.

    ``LAYOUT`` is included with ``TABLES`` because AWS bills Layout at no extra
    charge whenever Tables is enabled — there is never a reason to pay for Layout
    separately (§10.3 item 1).
    """
    if route == OCRRoute.TEXTRACT_TABLES:
        return ("TABLES", "LAYOUT")
    return ()


def submit(
    *,
    bucket: str,
    key: str,
    route: OCRRoute,
    idempotency_key: str,
    job_tag: str,
    existing_job_id: str | None = None,
) -> OCRSubmission:
    """
    Start one Textract job, or resume an existing one.

    ``existing_job_id`` short-circuits the whole call. That is the redelivery path:
    if ``pipeline_jobs`` already carries a job ID for this idempotency key, the
    work is in flight and submitting again would simply pay twice for it.
    """
    if existing_job_id:
        log.info(
            "resuming existing Textract job instead of re-submitting",
            extra={"job_id": existing_job_id, "idempotency_key": idempotency_key},
        )
        return OCRSubmission(existing_job_id, route, feature_types_for(route), already_running=True)

    if route not in (OCRRoute.TEXTRACT_TABLES, OCRRoute.TEXTRACT_TEXT):
        raise OCRError(f"route {route} does not submit to Textract")

    settings_obj = get_settings()
    if not (settings_obj.textract_sns_topic_arn and settings_obj.textract_sns_role_arn):
        raise OCRError(
            "TEXTRACT_SNS_TOPIC_ARN and TEXTRACT_SNS_ROLE_ARN are required. Completion "
            "arrives via SNS, never a polling loop (bottleneck B2)."
        )

    request = {
        # Textract reads the S3 object directly via IAM. No presigned URL, and the
        # bytes never leave the account (NFR-4).
        "DocumentLocation": {"S3Object": {"Bucket": bucket, "Name": key}},
        "ClientRequestToken": idempotency_key[:TOKEN_MAX],
        # JobTag comes back on the SNS notification, which is how the completion
        # consumer knows which document a job belongs to without a lookup table.
        "JobTag": job_tag[:64],
        "NotificationChannel": {
            "SNSTopicArn": settings_obj.textract_sns_topic_arn,
            "RoleArn": settings_obj.textract_sns_role_arn,
        },
    }

    client = _client()
    features = feature_types_for(route)
    try:
        if features:
            response = client.start_document_analysis(FeatureTypes=list(features), **request)
        else:
            response = client.start_document_text_detection(**request)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "IdempotentParameterMismatch":
            # The same token was used for different parameters. That means the
            # routing table or the document version changed without the key
            # changing — a bug worth surfacing loudly rather than working around.
            raise OCRError(
                f"Textract rejected the idempotency token for {key}: the same key was "
                f"used with different parameters. Check that route_config_version is "
                f"part of the key."
            ) from exc
        raise

    job_id = response["JobId"]
    log.info(
        "textract job submitted",
        extra={"job_id": job_id, "route": route.value, "key": key, "features": list(features)},
    )
    return OCRSubmission(job_id, route, features)


def fetch_results(job_id: str, route: OCRRoute) -> dict:
    """
    Retrieve a completed job's blocks, following every ``NextToken`` page.

    Called from the completion consumer *after* SNS reports SUCCEEDED, so this
    never blocks waiting for work — it only collects a result that already exists.
    """
    client = _client()
    getter = (
        client.get_document_analysis
        if route == OCRRoute.TEXTRACT_TABLES
        else client.get_document_text_detection
    )

    blocks: list[dict] = []
    next_token: str | None = None
    metadata: dict = {}
    pages = 0

    while True:
        kwargs = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            kwargs["NextToken"] = next_token
        response = getter(**kwargs)

        status = response.get("JobStatus")
        if status == "FAILED":
            raise OCRError(
                f"Textract job {job_id} failed: {response.get('StatusMessage', 'no detail')}"
            )
        if status == "IN_PROGRESS":
            raise OCRError(
                f"Textract job {job_id} is still IN_PROGRESS. The completion consumer "
                f"should only run after SNS reports SUCCEEDED."
            )

        blocks.extend(response.get("Blocks", []))
        metadata = response.get("DocumentMetadata", metadata)
        pages += 1
        next_token = response.get("NextToken")
        if not next_token:
            break

    log.info(
        "textract results fetched",
        extra={"job_id": job_id, "blocks": len(blocks), "result_pages": pages},
    )
    return {"JobId": job_id, "Blocks": blocks, "DocumentMetadata": metadata}


def compress_results(results: dict) -> bytes:
    """
    Gzip the raw OCR JSON before it is written.

    Textract output is extremely repetitive and compresses roughly 10-20x (§10.3
    item 5). Persisting it **immutably, before any processing** is what makes a
    re-extraction possible without a second OCR spend (§3.3 step 6).
    """
    return gzip.compress(json.dumps(results, separators=(",", ":")).encode("utf-8"), compresslevel=6)


def decompress_results(payload: bytes) -> dict:
    return json.loads(gzip.decompress(payload))
