"""
Django-to-worker handoff (§3.2 rule 2).

    Handoff is the SQS queue, not an HTTP call. Django enqueues on
    ``Document.status -> READY_FOR_PROCESSING`` and writes a ``pipeline_jobs``
    row; the worker consumes and advances that row through stages; Django's
    status endpoints read the same table. No synchronous cross-service
    dependency, and a worker restart loses nothing.

The idempotency key is computed here rather than in the worker so that the row
exists before the message does. If the enqueue fails, the job row is still there
and can be redriven; if the worker sees a message whose key already has an
``external_job_id``, it resumes instead of re-submitting (bottleneck B8).
"""

from __future__ import annotations

import hashlib
import json
import logging

import boto3

from shared.config import get_settings
from shared.enums import PipelineJobStatus, PipelineStage

log = logging.getLogger("cbc.queue")


def compute_idempotency_key(
    document_id: str, document_version_id: str | None, feature_set: str, route_config_version: str
) -> str:
    """
    ``sha256(document_version_id + feature_set + route_config_version)`` (§9 B8).

    The document's *S3 version-ID* is the input, not its row ID: a re-upload of
    the same logical document is genuinely new work and must not be deduplicated
    against the previous OCR run, while a redelivery of the same message must be.

    ``route_config_version`` is included because changing the OCR routing table
    changes which pages get analysed, so the same PDF under a new routing table is
    a different job with a different cost.
    """
    material = "|".join([document_id, document_version_id or "", feature_set, route_config_version])
    return hashlib.sha256(material.encode()).hexdigest()


def _queue_url(client, name: str) -> str:
    return client.get_queue_url(QueueName=name)["QueueUrl"]


def enqueue_document_ready(document, *, route_config_version: str = "v1") -> str | None:
    """
    Write the PREPROCESS job row, then publish the message.

    Order matters. The row is the durable record and the message is a hint: if the
    publish fails the work is still discoverable, whereas a message with no row
    would be work nothing can track.

    Returns the SQS message ID, or ``None`` when the job was already enqueued.
    """
    from .models import PipelineJob  # local import: avoids an app-loading cycle

    settings_obj = get_settings()
    idempotency_key = compute_idempotency_key(
        str(document.id), document.file_version_id, "PREPROCESS", route_config_version
    )

    job, created = PipelineJob.objects.get_or_create(
        document=document,
        stage=PipelineStage.PREPROCESS.value,
        defaults={
            "project": document.project,
            "status": PipelineJobStatus.PENDING.value,
            "idempotency_key": idempotency_key,
        },
    )
    if not created and job.status in {s.value for s in PipelineJobStatus.terminal()}:
        log.info("document %s already processed (%s); not re-enqueuing", document.id, job.status)
        return None

    body = {
        "EventType": "DocumentReady",
        "DocumentId": str(document.id),
        "ProjectId": str(document.project_id),
        "PipelineJobId": str(job.id),
        "S3Bucket": settings_obj.s3_source_bucket,
        "S3Key": document.file_key,
        "S3VersionId": document.file_version_id,
        "DocumentVersion": document.version,
        "IdempotencyKey": idempotency_key,
        "RouteConfigVersion": route_config_version,
    }

    client = boto3.client("sqs", **settings_obj.boto_kwargs_for("sqs"))
    response = client.send_message(
        QueueUrl=_queue_url(client, settings_obj.document_ready_queue),
        MessageBody=json.dumps(body),
    )
    message_id = response["MessageId"]
    log.info(
        "enqueued document-ready",
        extra={"document_id": str(document.id), "message_id": message_id, "job_id": str(job.id)},
    )
    return message_id
