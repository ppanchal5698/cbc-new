"""
Create the local MiniStack resources the pipeline expects (§8.3).

Runs once at ``docker compose up`` before the API and worker start. Idempotent —
every call is create-or-describe, so a restart is free.

This replaces the previous ``ensure_sqs_queue.py``, which created queues named
``cbc-copilot-jobs``/``cbc-copilot-dlq`` while every other file and ``.env``
referred to ``document-ready``/``document-ready-dlq``. The names come from
configuration here so they cannot drift again.

**Local only.** In dev, staging, and prod these resources are Terraform's job
(``infra/modules/queue``, ``infra/modules/storage``); this module refuses to run
anywhere the SDK is not pointed at an emulator.
"""

from __future__ import annotations

import json
import logging
import sys

import boto3
from botocore.exceptions import ClientError

from shared.config import ConfigError, get_settings

log = logging.getLogger("cbc.bootstrap")


def _ensure_bucket(s3, name: str) -> None:
    try:
        s3.head_bucket(Bucket=name)
        log.info("bucket exists: %s", name)
    except ClientError:
        s3.create_bucket(Bucket=name)
        # Versioning on both buckets mirrors production (§11.3). The source
        # bucket's Object Lock is NOT emulated locally; the intake path's
        # write-once guarantee is enforced in code as well, and that is what the
        # local tests exercise.
        s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
        log.info("bucket created: %s", name)


def _ensure_queue_pair(sqs, main: str, dlq: str, visibility: int, max_receive: int) -> dict:
    """
    Create the DLQ first, then the main queue with a redrive policy pointing at it.

    The redrive policy is the point of this function (C6, bottleneck B7). Without
    it one malformed document crash-loops the worker forever, blocking every other
    bid set — which has already happened once in this repository.
    """
    dlq_url = sqs.create_queue(QueueName=dlq)["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]

    main_url = sqs.create_queue(
        QueueName=main,
        Attributes={
            # 15 minutes: Textract on a large architectural set takes minutes, and
            # a redelivery mid-job is what bottleneck B8's idempotency key guards.
            "VisibilityTimeout": str(visibility),
            "RedrivePolicy": json.dumps(
                {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(max_receive)}
            ),
        },
    )["QueueUrl"]
    log.info("queue ready: %s (dlq %s, maxReceiveCount %s)", main, dlq, max_receive)
    return {"queue_url": main_url, "dlq_url": dlq_url, "dlq_arn": dlq_arn}


def bootstrap() -> dict:
    settings = get_settings()

    if not settings.aws_endpoint_url:
        raise ConfigError(
            "bootstrap_local requires LOCAL_AWS_ENDPOINT_URL (a MiniStack emulator). In dev, "
            "staging, and prod these resources are created by Terraform — see "
            "infra/modules/{storage,queue}."
        )

    s3 = boto3.client("s3", **settings.boto_kwargs_for("s3"))
    sqs = boto3.client("sqs", **settings.boto_kwargs_for("sqs"))
    sns = boto3.client("sns", **settings.boto_kwargs_for("sns"))

    _ensure_bucket(s3, settings.s3_source_bucket)
    _ensure_bucket(s3, settings.s3_derived_bucket)

    ready = _ensure_queue_pair(
        sqs,
        settings.document_ready_queue,
        settings.document_ready_dlq,
        settings.sqs_visibility_timeout_seconds,
        settings.sqs_max_receive_count,
    )
    # OCR completion arrives on its own queue via SNS, never a polling loop
    # (bottleneck B2). Its DLQ shares the same redrive contract.
    ocr = _ensure_queue_pair(
        sqs,
        settings.ocr_complete_queue,
        f"{settings.ocr_complete_queue}-dlq",
        settings.sqs_visibility_timeout_seconds,
        settings.sqs_max_receive_count,
    )

    topic_arn = sns.create_topic(Name="textract-completion")["TopicArn"]
    ocr_arn = sqs.get_queue_attributes(
        QueueUrl=ocr["queue_url"], AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    try:
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=ocr_arn)
    except ClientError as exc:  # emulators vary in subscription support
        log.warning("SNS->SQS subscription not created locally: %s", exc)

    log.info("local AWS resources ready")
    return {
        "source_bucket": settings.s3_source_bucket,
        "derived_bucket": settings.s3_derived_bucket,
        "document_ready_queue_url": ready["queue_url"],
        "ocr_complete_queue_url": ocr["queue_url"],
        "textract_sns_topic_arn": topic_arn,
    }


def main(*, attempts: int = 30, delay: float = 2.0) -> int:
    """
    Retry until the emulator answers, then create everything.

    This is the stack's readiness gate. The ministack image ships no HTTP client,
    so a container healthcheck cannot probe it; succeeding here proves S3, SQS,
    and SNS are all actually usable, which is what the api and pipeline services
    wait on.
    """
    import time

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            for key, value in bootstrap().items():
                print(f"  {key:<28} {value}")
            return 0
        except ConfigError:
            raise  # a misconfiguration will not fix itself by waiting
        except Exception as exc:  # noqa: BLE001 - emulator may still be starting
            last_error = exc
            log.info("emulator not ready (attempt %s/%s): %s", attempt, attempts, exc)
            time.sleep(delay)
    log.error("bootstrap failed after %s attempts: %s", attempts, last_error)
    return 1


if __name__ == "__main__":
    sys.exit(main())
