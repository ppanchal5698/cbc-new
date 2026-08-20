"""
FastAPI pipeline worker (§3.1, §3.2).

The HTTP surface exists only for health checks and operational introspection; all
real work arrives on SQS. Consumers run as lifespan tasks so the process has one
lifecycle and a container restart cleanly stops polling.

This service runs on its own EC2 instance (decision D9): the worker's memory
profile is spiky — a 200-page plan set, PyMuPDF rasterisation, and buffered OCR
JSON all peak together — and colocating it with the API means a single large bid
set degrades every estimator's page loads.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import django

# Django is bootstrapped before any stage import: the worker uses the Django ORM
# for row writes against Django-migrated tables. It never runs a migration
# itself — Django owns the schema (§3.2 rule 1, ADR-0001).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from fastapi import FastAPI  # noqa: E402

from pipeline.consumers.sqs_consumer import consume_forever  # noqa: E402
from pipeline.observability.logging_setup import configure  # noqa: E402
from pipeline.routing import load_routing_table  # noqa: E402
from shared.config import get_settings  # noqa: E402

log = logging.getLogger("cbc.pipeline")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure(service="pipeline")
    settings_obj = get_settings()

    # Fail fast if the routing table is missing: §4.4 has no hardcoded fallback,
    # and a worker that silently routed every page to Textract would be the
    # expensive failure bottleneck B1 exists to prevent.
    table = load_routing_table()
    log.info(
        "pipeline starting",
        extra={
            "environment": settings_obj.environment,
            "routing_table": table.version,
            "routing_hash": table.content_hash,
        },
    )

    tasks = [
        asyncio.create_task(
            consume_forever(settings_obj.document_ready_queue), name="document-ready"
        ),
        asyncio.create_task(
            consume_forever(settings_obj.ocr_complete_queue), name="ocr-complete"
        ),
    ]
    app.state.consumers = tasks
    app.state.routing_table = table
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log.info("pipeline stopped")


app = FastAPI(
    title="CBC Copilot Pipeline",
    description="Preprocess, OCR, extract, link, match, price. Work arrives on SQS.",
    version="3.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    """Liveness plus whether the SQS consumers are still running."""
    tasks = getattr(app.state, "consumers", [])
    alive = [task.get_name() for task in tasks if not task.done()]
    dead = [task.get_name() for task in tasks if task.done()]
    return {
        "status": "ok" if not dead else "degraded",
        "consumers_running": alive,
        "consumers_stopped": dead,
    }


@app.get("/routing")
def routing() -> dict:
    """
    The routing table in force (§4.4).

    Exposed because "why did the system skip page 47?" is answered by the manifest
    plus this — and an operator needs to see the version and hash actually loaded,
    not the one they think they deployed.
    """
    table = getattr(app.state, "routing_table", None) or load_routing_table()
    return {
        "version": table.version,
        "content_hash": table.content_hash,
        "source": table.source,
    }
