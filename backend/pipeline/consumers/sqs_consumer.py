"""
SQS consumer (§3.2 rule 2, bottleneck B7).

Runs as a FastAPI lifespan task. Long-polls, dispatches to the coordinator, and
deletes only on success — a failure leaves the message to reappear after the
visibility timeout and, after ``maxReceiveCount`` deliveries, land on the DLQ.

Three things this fixes in the previous implementation:

* It **retries** rather than returning permanently when the queue is briefly
  unavailable. The old consumer returned on the first ``get_queue_url`` failure,
  leaving the worker silently dead for the lifetime of the process while the
  container still reported healthy.
* Blocking boto3 calls run in a thread, so one slow receive does not stall the
  event loop.
* A message that no handler recognises is **deleted**, not retried. Redelivering
  an unparseable message three times before the DLQ accepts it is three wasted
  visibility timeouts and a misleading DLQ entry.
"""

from __future__ import annotations

import asyncio
import json
import logging

import boto3
from botocore.exceptions import ClientError

from pipeline.coordinator import UnknownMessage, handle_message
from pipeline.observability.logging_setup import job_context
from shared.config import get_settings

log = logging.getLogger("cbc.consumer")

#: Long-poll duration. 20s is the SQS maximum and the cheapest way to wait.
WAIT_SECONDS = 20
#: Backoff when the queue itself is unreachable.
RECONNECT_DELAY = 5


async def _to_thread(func, /, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def resolve_queue_url(client, queue_name: str) -> str | None:
    try:
        response = await _to_thread(client.get_queue_url, QueueName=queue_name)
        return response["QueueUrl"]
    except ClientError as exc:
        log.warning("queue %s not reachable yet: %s", queue_name, exc)
        return None


async def consume_forever(queue_name: str, *, max_messages: int = 5) -> None:
    """
    Poll one queue until cancelled.

    The consumer is horizontally safe: per-document idempotency keys mean two
    workers receiving the same document cannot double-submit to Textract, which is
    what lets §3.5 add worker instances when the backlog grows.
    """
    settings_obj = get_settings()
    client = boto3.client("sqs", **settings_obj.boto_kwargs_for("sqs"))
    queue_url: str | None = None

    while True:
        try:
            if queue_url is None:
                queue_url = await resolve_queue_url(client, queue_name)
                if queue_url is None:
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
                log.info("listening on %s", queue_url)

            response = await _to_thread(
                client.receive_message,
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=WAIT_SECONDS,
                VisibilityTimeout=settings_obj.sqs_visibility_timeout_seconds,
                AttributeNames=["ApproximateReceiveCount"],
            )

            for message in response.get("Messages", []):
                await _process(client, queue_url, message, settings_obj)

        except asyncio.CancelledError:
            log.info("consumer for %s cancelled", queue_name)
            raise
        except ClientError as exc:
            log.warning("SQS error on %s; will retry: %s", queue_name, exc)
            queue_url = None
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception:
            log.exception("unexpected consumer error on %s", queue_name)
            await asyncio.sleep(RECONNECT_DELAY)


async def _process(client, queue_url: str, message: dict, settings_obj) -> None:
    receipt = message["ReceiptHandle"]
    receive_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    try:
        body = json.loads(message["Body"])
    except json.JSONDecodeError:
        log.error("message body is not JSON; deleting rather than looping")
        await _to_thread(client.delete_message, QueueUrl=queue_url, ReceiptHandle=receipt)
        return

    with job_context(pipeline_job_id=str(body.get("PipelineJobId", ""))):
        try:
            await handle_message(body)
            await _to_thread(client.delete_message, QueueUrl=queue_url, ReceiptHandle=receipt)
        except UnknownMessage as exc:
            # Not a transient failure: redelivering will not make it parseable.
            log.error("unroutable message deleted: %s", exc)
            await _to_thread(client.delete_message, QueueUrl=queue_url, ReceiptHandle=receipt)
        except Exception:
            remaining = settings_obj.sqs_max_receive_count - receive_count
            log.exception(
                "message failed; leaving on queue",
                extra={"receive_count": receive_count, "deliveries_before_dlq": max(remaining, 0)},
            )
            if remaining <= 0:
                # Next delivery sends it to the DLQ. Say so now, while the reason
                # is still in this log line (bottleneck B7).
                log.error("message will move to the DLQ on the next delivery")
