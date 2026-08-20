"""
Structured logging and stage timing (§11.5).

JSON to stdout, picked up by the CloudWatch agent. The correlation ID is the
``pipeline_job_id``, which is what lets "where did the four minutes go" have an
answer: every log line and every X-Ray segment for one document carries the same
key.

The two metrics worth watching most closely are **citation-rejection rate** and
**estimator-correction rate**. A rise in the first means the model or prompt has
drifted. A rise in the second means the system is confidently wrong — the failure
mode NFR-2 exists to prevent, and the one that erodes adoption fastest.
"""

from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager

from shared.config import get_settings

#: Set once per message, read by the filter on every line.
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")
document_id: contextvars.ContextVar[str] = contextvars.ContextVar("document_id", default="")
stage_name: contextvars.ContextVar[str] = contextvars.ContextVar("stage", default="")


class CorrelationFilter(logging.Filter):
    """Attach the pipeline job, document, and stage to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.pipeline_job_id = correlation_id.get()
        record.document_id = document_id.get()
        record.stage = stage_name.get()
        return True


def configure(service: str = "pipeline") -> None:
    """Install JSON (or plain) logging for the worker process."""
    settings_obj = get_settings()
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationFilter())

    if settings_obj.log_format == "json":
        from pythonjsonlogger import json as jsonlogger

        handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s "
                "%(pipeline_job_id)s %(document_id)s %(stage)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
                static_fields={"service": service},
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(stage)s] %(name)s: %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings_obj.log_level)
    logging.getLogger("botocore").setLevel("WARNING")
    logging.getLogger("boto3").setLevel("WARNING")
    logging.getLogger("urllib3").setLevel("WARNING")


@contextmanager
def job_context(*, pipeline_job_id: str = "", doc_id: str = "", stage: str = ""):
    """Bind correlation values for the duration of one message or stage."""
    tokens = [
        correlation_id.set(pipeline_job_id or correlation_id.get()),
        document_id.set(doc_id or document_id.get()),
        stage_name.set(stage or stage_name.get()),
    ]
    try:
        yield
    finally:
        correlation_id.reset(tokens[0])
        document_id.reset(tokens[1])
        stage_name.reset(tokens[2])


@contextmanager
def timed(stage: str, logger: logging.Logger | None = None):
    """
    Time one stage and log its duration.

    Every stage emits latency so that NFR-6 ("a reviewable draft in minutes") has a
    measurement rather than an impression (§3.3 step 14).
    """
    logger = logger or logging.getLogger("cbc.pipeline")
    started = time.perf_counter()
    with job_context(stage=stage):
        logger.info("stage started")
        try:
            yield
        except Exception:
            logger.exception(
                "stage failed", extra={"duration_ms": int((time.perf_counter() - started) * 1000)}
            )
            raise
        logger.info(
            "stage complete",
            extra={"duration_ms": int((time.perf_counter() - started) * 1000)},
        )
