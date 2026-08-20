"""
Quote export (FR-10, bottleneck B14).

WeasyPrint on a request thread blocks a worker for seconds on a large quote, so
the render is enqueued and the caller gets a job ID — the same pattern as every
other long operation in this system.

The rendered PDF routes back to whoever initiated the request
(Kellan/Matt/Rebecca/Tina), **not a group inbox**.
"""

from __future__ import annotations

import json
import logging

import boto3

from shared.config import get_settings

log = logging.getLogger("cbc.export")


def enqueue_quote_export(quote, *, requested_by=None) -> str:
    """
    Queue the render. Returns the SQS message ID.

    The recipient is resolved and stored here rather than at render time so the
    record of who it was sent to survives a later edit to the project.
    """
    settings_obj = get_settings()
    recipient = quote.project.initiator_email

    quote.exported_to_email = recipient
    quote.save(update_fields=["exported_to_email", "updated_at"])

    body = {
        "EventType": "QuoteExportRequested",
        "QuoteId": str(quote.id),
        "ProjectId": str(quote.project_id),
        "RecipientEmail": recipient,
        "RequestedBy": str(getattr(requested_by, "id", "")) or None,
    }
    client = boto3.client("sqs", **settings_obj.boto_kwargs)
    queue_url = client.get_queue_url(QueueName=settings_obj.document_ready_queue)["QueueUrl"]
    message_id = client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))["MessageId"]

    log.info(
        "quote export enqueued",
        extra={"quote_id": str(quote.id), "recipient": recipient, "message_id": message_id},
    )
    return message_id
