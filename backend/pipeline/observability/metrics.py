"""
Business and cost metrics (§11.5).

Metrics are emitted as **CloudWatch Embedded Metric Format** — a JSON object on
stdout that the CloudWatch agent parses into real metrics. No `PutMetricData`
call, so no API latency in the hot path, no throttling at 150 TPS, no IAM grant
beyond the log stream the worker already writes to, and the whole thing still
works locally where it is just a line in `docker compose logs`.

**The two headline signals.**

*Citation-rejection rate* is the earliest warning that a prompt edit or a model
version bump has degraded quality. It moves before accuracy does, because the
§5.6 gate catches ungrounded values that a human reviewer might have waved
through.

*Estimator-correction rate* is the one that matters commercially. It says the
system was confidently wrong: it produced a value, did not flag it, and a human
had to fix it. That is the NFR-2 failure mode, and it is the number that decides
whether estimators keep using the tool.

Everything else here exists to answer "why did this bid set cost that much" and
"where did the four minutes go" without anyone opening a database.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from decimal import Decimal

from pipeline.observability.logging_setup import correlation_id, document_id

log = logging.getLogger("cbc.metrics")

#: CloudWatch namespace. One namespace for the whole system: splitting metrics
#: across namespaces makes a single dashboard impossible for no benefit.
NAMESPACE = "CBCCopilot"

#: EMF units, per metric name. CloudWatch will happily store a dimensionless
#: number and then render a dollar amount as "Count" on every dashboard forever.
UNITS = {
    "OCRCostUSD": "None",
    "BedrockCostUSD": "None",
    "DocumentCostUSD": "None",
    "StageDurationMs": "Milliseconds",
    "DocumentLatencyMs": "Milliseconds",
    "PagesReceived": "Count",
    "PagesOCR": "Count",
    "PagesSkipped": "Count",
    "OpeningsExtracted": "Count",
    "FieldsEmitted": "Count",
    "FieldsRejectedCitation": "Count",
    "FieldsRejectedGrounding": "Count",
    "FieldsFlagged": "Count",
    "CitationRejectionRate": "Percent",
    "EstimatorCorrectionRate": "Percent",
    "InputTokens": "Count",
    "CachedInputTokens": "Count",
    "OutputTokens": "Count",
}


def emit(metrics: dict[str, float | int | Decimal], **dimensions: str) -> None:
    """
    Write one EMF record.

    Dimensions are deliberately low-cardinality. A ``document_id`` dimension would
    mint a new CloudWatch metric per bid set, which is both expensive and useless
    — you cannot alarm on a metric that exists once. The document id goes in the
    record as a plain property instead, where it is searchable in Logs Insights
    and costs nothing.
    """
    if not metrics:
        return

    dimensions = {k: v for k, v in dimensions.items() if v}
    record: dict = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dimensions)] if dimensions else [[]],
                    "Metrics": [
                        {"Name": name, "Unit": UNITS.get(name, "None")} for name in metrics
                    ],
                }
            ],
        },
        **dimensions,
        **{name: float(value) for name, value in metrics.items()},
    }

    # Searchable context, not dimensions. See the docstring.
    if job := correlation_id.get():
        record["pipeline_job_id"] = job
    if doc := document_id.get():
        record["document_id"] = doc

    print(json.dumps(record), file=sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# The named metrics. Functions rather than bare emit() calls so the metric names
# have exactly one spelling — a typo'd metric name does not error, it silently
# creates a second metric that nobody is watching.
# ---------------------------------------------------------------------------

def record_triage(*, pages: int, pages_ocr: int, pages_skipped: int, cost: Decimal) -> None:
    """§4 outcome for one document. The cost saving lives here."""
    emit(
        {
            "PagesReceived": pages,
            "PagesOCR": pages_ocr,
            "PagesSkipped": pages_skipped,
            "OCRCostUSD": cost,
        },
        stage="preprocess",
    )


def record_extraction(
    *,
    openings: int,
    fields_emitted: int,
    fields_rejected_citation: int,
    fields_rejected_grounding: int,
    fields_flagged: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    cost: Decimal | None,
) -> None:
    """§5 outcome for one document, including the first headline signal."""
    rejected = fields_rejected_citation + fields_rejected_grounding
    metrics: dict[str, float | int | Decimal] = {
        "OpeningsExtracted": openings,
        "FieldsEmitted": fields_emitted,
        "FieldsRejectedCitation": fields_rejected_citation,
        "FieldsRejectedGrounding": fields_rejected_grounding,
        "FieldsFlagged": fields_flagged,
        "InputTokens": input_tokens,
        "CachedInputTokens": cached_input_tokens,
        "OutputTokens": output_tokens,
    }
    if fields_emitted:
        metrics["CitationRejectionRate"] = 100.0 * rejected / fields_emitted
    if cost is not None:
        metrics["BedrockCostUSD"] = cost

    emit(metrics, stage="extract")


def record_estimator_correction(*, field_name: str, corrections: int, reviewed: int) -> None:
    """
    The second headline signal, emitted from the review path (FR-13).

    Dimensioned by field because the aggregate hides the thing worth knowing: a
    5% correction rate spread evenly is a tuning problem, and a 5% rate that is
    entirely fire ratings is a §5.8 problem.
    """
    metrics: dict[str, float | int | Decimal] = {}
    if reviewed:
        metrics["EstimatorCorrectionRate"] = 100.0 * corrections / reviewed
    emit(metrics, field=field_name)


def record_document_complete(*, latency_ms: int, total_cost: Decimal) -> None:
    """End-to-end, which is what NFR-6 is actually written against."""
    emit({"DocumentLatencyMs": latency_ms, "DocumentCostUSD": total_cost})


# ---------------------------------------------------------------------------
# X-Ray
# ---------------------------------------------------------------------------

#: Set by ``configure_xray`` when tracing is actually available. Until then
#: ``trace_segment`` must not touch the SDK at all.
#:
#: Guarding on a flag rather than on catching exceptions is not defensive
#: duplication. With no active segment the recorder does not raise — its default
#: ``context_missing`` behaviour is to *log an error* and return None. So a
#: try/except around it silences nothing, and every stage of every local run
#: emits an ERROR line about a missing segment. Error-level noise that is known
#: to be meaningless is worse than no telemetry: it trains everyone reading the
#: logs to skim past the level that matters.
_tracing_enabled = False


@contextmanager
def trace_segment(name: str):
    """
    Open an X-Ray subsegment, or do nothing.

    "Do nothing" is the normal case locally and in tests, where there is no daemon
    and ``configure_xray`` deliberately does not enable tracing. Observability
    that can take down — or shout over — the thing it observes is a liability.
    """
    if not _tracing_enabled:
        yield None
        return

    try:
        from aws_xray_sdk.core import xray_recorder
    except ImportError:
        yield None
        return

    try:
        subsegment = xray_recorder.begin_subsegment(name)
    except Exception:  # noqa: BLE001 - no daemon, no active segment, sampling off
        yield None
        return

    if subsegment is None:
        yield None
        return

    try:
        if job := correlation_id.get():
            subsegment.put_annotation("pipeline_job_id", job)
        if doc := document_id.get():
            subsegment.put_annotation("document_id", doc)
        yield subsegment
    except Exception as exc:
        try:
            subsegment.add_exception(exc, [])
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            xray_recorder.end_subsegment()
        except Exception:  # noqa: BLE001
            pass


def configure_xray(service: str = "cbc-pipeline") -> None:
    """
    Turn on tracing outside local development.

    Called once at worker start-up. Failure here is logged and swallowed for the
    same reason as above.
    """
    global _tracing_enabled

    from shared.config import get_settings

    settings = get_settings()
    if settings.environment == "local":
        log.debug("X-Ray disabled in local environment")
        return

    try:
        from aws_xray_sdk.core import patch_all, xray_recorder

        # IGNORE_ERROR, not LOG_ERROR: a subsegment opened outside any segment is
        # an ordinary condition for a queue worker between messages, and logging
        # it at ERROR would bury the failures that matter.
        xray_recorder.configure(service=service, context_missing="IGNORE_ERROR")
        # boto3 and requests, so a slow Textract or Bedrock call shows up as its
        # own span rather than as unexplained time inside a stage.
        patch_all()
        _tracing_enabled = True
        log.info("X-Ray tracing enabled", extra={"service": service})
    except Exception as exc:  # noqa: BLE001
        log.warning("X-Ray unavailable, continuing without tracing: %s", exc)
