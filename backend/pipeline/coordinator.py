"""
The pipeline state machine (§3.3).

    preprocess → raster → ocr → (SNS) → normalize → extract → link → match → price

Each stage writes its own ``pipeline_jobs`` row, so Django's status endpoints read
the same table the worker writes and a restart loses nothing (§3.2 rule 2).

The document pauses after OCR submission: completion arrives on SNS → SQS and is
picked up by :mod:`pipeline.consumers.ocr_complete`, not by a polling loop
(bottleneck B2).
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

from projects.queue_ops import compute_idempotency_key

from pipeline.db import repository as repo
from pipeline.observability.logging_setup import job_context, timed
from pipeline.routing import load_routing_table
from pipeline.stages import ocr as ocr_stage
from pipeline.stages import preprocess as preprocess_stage
from pipeline.stages import raster as raster_stage
from shared.config import get_settings
from shared.enums import DocumentStatus, OCRRoute, PipelineJobStatus, PipelineStage
from shared.s3_keys import (
    get_ocr_result_key,
    get_ocr_subset_key,
    get_raster_ocr_input_key,
    get_raster_thumb_key,
    get_raster_viewer_key,
    get_repaired_pdf_key,
)

log = logging.getLogger("cbc.coordinator")


class UnknownMessage(ValueError):
    """The message does not match any handler. Deleted rather than retried."""


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------

def parse_message(body: dict) -> tuple[str, dict]:
    """
    Return ``(event_type, payload)``.

    SNS notifications arrive wrapped in an SQS envelope; Textract's own payload is
    a JSON string inside ``Message``.
    """
    if body.get("Type") == "Notification" and "Message" in body:
        inner = json.loads(body["Message"])
        if "JobId" in inner and "Status" in inner:
            return "TextractCompleted", inner
        return "SNSNotification", inner

    event_type = body.get("EventType")
    if not event_type:
        raise UnknownMessage(f"message has no EventType: {list(body)[:6]}")
    return event_type, body


async def handle_message(body: dict) -> None:
    """
    Dispatch one SQS message. Raising leaves it on the queue for redelivery.

    Handlers run in a worker thread. Two reasons, and both matter:

    * The Django ORM is synchronous and refuses to run inside an event loop.
    * Every stage is genuinely blocking work — PyMuPDF rasterisation, S3 transfers,
      Textract calls. Running them on the loop would stall polling for the minutes
      a 200-page plan set takes, which is the concurrency problem bottleneck B2
      exists to solve.

    ``asyncio.to_thread`` copies the current context, so the correlation IDs bound
    below still appear on every log line the handler emits.
    """
    event_type, payload = parse_message(body)

    handlers = {
        "DocumentReady": handle_document_ready,
        "TextractCompleted": handle_textract_completed,
        "QuoteExportRequested": handle_quote_export,
    }
    handler = handlers.get(event_type)
    if handler is None:
        raise UnknownMessage(f"no handler for EventType={event_type!r}")

    with job_context(
        pipeline_job_id=str(payload.get("PipelineJobId", "")),
        doc_id=str(payload.get("DocumentId", "")),
    ):
        await asyncio.to_thread(_run_handler, handler, payload)


def _run_handler(handler, payload: dict) -> None:
    """
    Run one handler and close its database connection afterwards.

    Django opens a connection per thread and keeps it in thread-local storage. A
    thread-pool worker that never closes leaks one connection per thread against a
    small RDS instance — bottleneck B10 arriving by the back door.
    """
    from django.db import close_old_connections

    close_old_connections()
    try:
        handler(payload)
    finally:
        close_old_connections()


# ---------------------------------------------------------------------------
# Stage 1-3: preprocess, raster, OCR submission
# ---------------------------------------------------------------------------

def handle_document_ready(payload: dict) -> None:
    """
    Preprocess, rasterise, and submit only the pages that earn it.

    The order is deliberate: the manifest is persisted **before** the first
    Textract call, so if OCR fails there is still an audit record of what the
    system decided to read and why (§4.1).
    """
    from projects.models import Document
    from projects.storage_ops import get_source_document, put_derived

    settings_obj = get_settings()
    table = load_routing_table()
    document = Document.objects.select_related("project").get(id=payload["DocumentId"])

    job = repo.get_or_create_job(
        document, PipelineStage.PREPROCESS, idempotency_key=payload.get("IdempotencyKey")
    )
    if job.status == PipelineJobStatus.COMPLETED.value:
        log.info("preprocess already complete; skipping to OCR")
    else:
        repo.start_job(job)
        try:
            with timed("PREPROCESS", log):
                data = get_source_document(document.file_key, document.file_version_id)

                usable, was_repaired = preprocess_stage.validate_pdf(data)
                if was_repaired:
                    # The repaired copy lands in derived; source stays untouched.
                    put_derived(
                        get_repaired_pdf_key(str(document.id), document.version),
                        usable,
                        content_type="application/pdf",
                    )
                    document.was_repaired = True
                    document.save(update_fields=["was_repaired", "updated_at"])

                probes = preprocess_stage.analyze_document(
                    usable,
                    table=table,
                    max_cost_usd=settings_obj.max_ocr_cost_per_document_usd,
                )

            with timed("RASTER", log):
                raster_keys = _render_and_upload(document, usable, probes, settings_obj)

            repo.write_manifest(document, probes, raster_keys=raster_keys)
            repo.complete_job(job)
            repo.set_document_status(document, DocumentStatus.PROCESSING)

        except preprocess_stage.EncryptedDocument as exc:
            repo.fail_job(job, str(exc), quarantine=True)
            repo.set_document_status(document, DocumentStatus.QUARANTINED, str(exc))
            return  # a password will not appear on retry; do not redeliver
        except preprocess_stage.BudgetExceeded as exc:
            repo.fail_job(job, str(exc), quarantine=True)
            repo.set_document_status(document, DocumentStatus.QUARANTINED, str(exc))
            return  # retrying spends the money the guard just refused
        except Exception as exc:
            repo.fail_job(job, str(exc))
            raise

    _submit_ocr(document, payload, table)


def _render_and_upload(document, data: bytes, probes, settings_obj) -> dict:
    """Render every tier once and upload to derived. Returns page -> {tier: key}."""
    from projects.storage_ops import put_derived

    # Only pages the free classification tiers left unresolved need a thumbnail
    # for Tier-4 Haiku; rendering one per page would pay for 200 images to
    # classify two (§4.3).
    thumbs_for = {
        probe.page_number
        for probe in probes
        if preprocess_stage.needs_model_classification(probe, load_routing_table())
    }

    keys: dict[int, dict[str, str]] = {}
    for page_number, tier, image_bytes in raster_stage.render_document(
        data,
        probes,
        max_long_edge_px=settings_obj.raster_max_long_edge_px,
        include_thumbs_for=thumbs_for,
    ):
        if tier is raster_stage.VIEWER:
            key = get_raster_viewer_key(str(document.id), document.version, page_number)
        elif tier is raster_stage.THUMB:
            key = get_raster_thumb_key(str(document.id), document.version, page_number)
        else:
            key = get_raster_ocr_input_key(str(document.id), document.version, page_number)

        put_derived(key, image_bytes, content_type=raster_stage.CONTENT_TYPES[tier.image_format])
        keys.setdefault(page_number, {})[tier.name] = key
    return keys


def _routed_pages(document) -> list[tuple[int, str]]:
    """
    ``(page_number, ocr_route)`` for every page triage routed to OCR, in page order.

    Read from the persisted manifest rather than recomputed: the manifest is the
    record of what the system decided and what the estimator is shown in the UI,
    and replaying against a fresh classification could silently disagree with it.

    The ordering is load-bearing — it *is* the map from submitted-subset page N
    back to the document-global page number (§4.6 rule 3).
    """
    from projects.models import DocumentManifest

    return list(
        DocumentManifest.objects.filter(
            document=document,
            ocr_route__in=(OCRRoute.TEXTRACT_TABLES.value, OCRRoute.TEXTRACT_TEXT.value),
        )
        .order_by("page_number")
        .values_list("page_number", "ocr_route")
    )


def _submit_ocr(document, payload: dict, table) -> None:
    """
    Submit **only the pages triage routed**, once, idempotently (B1 and B8).

    The routed pages are extracted into a small derived PDF and *that* is what
    Textract receives. This is the step that turns §4 from a manifest annotation
    into the 23x cost reduction it is supposed to be: Textract bills per page it
    processes, so submitting the source PDF pays for all 200 pages of a plan set
    in order to read the six that carry a schedule.

    Note what is *not* here: no wait, no poll, no sleep. The worker submits and
    moves on to the next message; completion arrives on SNS (B2).
    """
    from projects.models import DocumentManifest
    from projects.storage_ops import get_source_document, put_derived

    routes = set(
        DocumentManifest.objects.filter(document=document).values_list("ocr_route", flat=True)
    )
    # ponytail: one subset, one job, and TABLES features whenever any page needs
    # them — so scanned spec pages ride along at the Tables rate rather than the
    # 10x cheaper DetectDocumentText rate. A second job would need `route` in
    # uniq_job_document_stage and in the SNS JobTag, for a saving that is second
    # order once the page count has already dropped ~10x. Split it if the
    # per-route page counts in `make cost-report` ever say otherwise.
    if OCRRoute.TEXTRACT_TABLES.value in routes:
        route = OCRRoute.TEXTRACT_TABLES
    elif OCRRoute.TEXTRACT_TEXT.value in routes:
        route = OCRRoute.TEXTRACT_TEXT
    else:
        route = None

    # §9 B8: sha256(document_version_id + feature_set + route_config_version).
    # The feature set is part of the material precisely because it differs per
    # stage — PREPROCESS and OCR are different work on the same document, and the
    # column is globally unique.
    feature_set = ",".join(ocr_stage.feature_types_for(route)) if route else "NONE"
    idempotency_key = compute_idempotency_key(
        str(document.id), document.file_version_id, f"OCR:{feature_set}", table.content_hash
    )
    job = repo.get_or_create_job(
        document, PipelineStage.OCR, idempotency_key=idempotency_key
    )
    if job.status in (PipelineJobStatus.COMPLETED.value, PipelineJobStatus.SKIPPED.value):
        return

    if route is None:
        # Every page was native text or skipped. That is a complete, correct
        # outcome and the cheapest possible one — not a failure.
        log.info("no page requires Textract; skipping OCR entirely")
        job.status = PipelineJobStatus.SKIPPED.value
        job.save(update_fields=["status", "updated_at"])
        return

    repo.start_job(job)
    routed = _routed_pages(document)
    pages = [page for page, _ in routed]

    if get_settings().fake_ocr:
        # Offline loop: synthesise blocks from the PDF's own text layer and run
        # the completion path directly. Everything downstream — normalisation,
        # element_path construction, the viewer overlay — is exercised for real.
        from pipeline.stages import fake_ocr as fake

        data = get_source_document(document.file_key, document.file_version_id)
        results = fake.synthesize(data, routed)

        key = get_ocr_result_key(str(document.id), document.version)
        version_id = put_derived(
            key,
            ocr_stage.compress_results(results),
            content_type="application/json",
            content_encoding="gzip",
        )
        document.ocr_result_key = key
        document.ocr_result_version_id = version_id
        document.save(update_fields=["ocr_result_key", "ocr_result_version_id", "updated_at"])
        repo.record_external_job_id(job, "FAKE_OCR")
        repo.complete_job(job, cost_actual=Decimal("0"))
        normalise_document(document, results)
        return

    try:
        data = get_source_document(document.file_key, document.file_version_id)
        subset = preprocess_stage.subset_pdf(data, pages)
        ocr_stage.assert_within_limits(pages=len(pages), size_bytes=len(subset))

        subset_key = get_ocr_subset_key(str(document.id), document.version)
        put_derived(subset_key, subset, content_type="application/pdf")
        log.info(
            "submitting routed subset rather than the source document",
            extra={
                "pages_submitted": len(pages),
                "pages_total": document.page_count,
                "subset_key": subset_key,
            },
        )

        submission = ocr_stage.submit(
            bucket=get_settings().s3_derived_bucket,
            key=subset_key,
            route=route,
            idempotency_key=job.idempotency_key or str(job.id),
            job_tag=str(document.id),
            existing_job_id=job.external_job_id,
        )
        # BEFORE the call is considered complete (B8).
        repo.record_external_job_id(job, submission.job_id)
    except ocr_stage.DocumentTooLarge as exc:
        # Retrying cannot make the document smaller.
        repo.fail_job(job, str(exc), quarantine=True)
        repo.set_document_status(document, DocumentStatus.QUARANTINED, str(exc))
        return
    except Exception as exc:
        repo.fail_job(job, str(exc))
        raise


# ---------------------------------------------------------------------------
# Stage 4: OCR completion → normalise
# ---------------------------------------------------------------------------

def handle_textract_completed(payload: dict) -> None:
    """
    Fetch results, persist them immutably, then normalise (§3.3 steps 6-7).

    The raw JSON is written **before** any processing so a re-extraction never
    needs a second OCR spend.
    """
    from projects.models import Document
    from projects.storage_ops import put_derived

    job_id = payload["JobId"]
    status = payload.get("Status")
    document_id = payload.get("JobTag")

    document = Document.objects.get(id=document_id)
    job = repo.get_or_create_job(document, PipelineStage.OCR)

    if status != "SUCCEEDED":
        message = f"Textract job {job_id} finished with status {status}"
        repo.fail_job(job, message, quarantine=True)
        repo.set_document_status(document, DocumentStatus.FAILED, message)
        return

    route = (
        OCRRoute.TEXTRACT_TABLES
        if payload.get("API", "").endswith("Analysis")
        else OCRRoute.TEXTRACT_TABLES
    )

    with timed("OCR", log):
        results = ocr_stage.fetch_results(job_id, route)
        key = get_ocr_result_key(str(document.id), document.version)
        version_id = put_derived(
            key,
            ocr_stage.compress_results(results),
            content_type="application/json",
            content_encoding="gzip",
        )
        document.ocr_result_key = key
        document.ocr_result_version_id = version_id
        document.save(update_fields=["ocr_result_key", "ocr_result_version_id", "updated_at"])
        repo.complete_job(job)

    normalise_document(document, results)


def normalise_document(document, results: dict) -> int:
    """
    Flatten OCR blocks into ``doc_elements`` with stable positional paths.

    Textract numbered the pages of the subset it received from 1; the routed-page
    list maps them back to the document-global numbers a citation has to use
    (§4.6 rule 3).
    """
    from pipeline.stages import normalize as normalize_stage
    from shared.db_url import to_psycopg_dsn

    job = repo.get_or_create_job(document, PipelineStage.NORMALIZE)
    repo.start_job(job)
    try:
        with timed("NORMALIZE", log):
            elements = normalize_stage.parse_blocks(
                results.get("Blocks", []), submitted_pages=[p for p, _ in _routed_pages(document)]
            )
            written = normalize_stage.bulk_insert_elements(
                to_psycopg_dsn(get_settings().database_url), str(document.id), elements
            )
        repo.complete_job(job)
    except Exception as exc:
        repo.fail_job(job, str(exc))
        raise

    # Outside the block above on purpose. Extraction failing does not un-write
    # fourteen thousand committed elements, and marking NORMALIZE failed for
    # something EXTRACT did sends whoever reads the job row to the wrong stage —
    # which is the entire value of having per-stage rows.
    _extract_and_link(document)
    repo.set_document_status(document, DocumentStatus.PROCESSED)
    return written


def _extract_and_link(document) -> None:
    """
    Run EXTRACT and LINK, or record honestly that they were skipped.

    Bedrock model IDs are resolved at deploy and pinned in SSM (C5); with none
    pinned there is nothing to invoke. Normalisation has still succeeded and the
    source viewer works, so the document is not failed — but the skip is written
    to the job row rather than passing silently, because a document that looks
    PROCESSED with no openings is exactly the silent omission NFR-2 forbids.
    """
    from pipeline.stages import run_extraction
    from shared.config import ConfigError

    try:
        run_extraction.run(document)
    except ConfigError as exc:
        for stage in (PipelineStage.EXTRACT, PipelineStage.LINK):
            skipped = repo.get_or_create_job(document, stage)
            skipped.status = PipelineJobStatus.SKIPPED.value
            skipped.error_detail = str(exc)[:4000]
            skipped.save(update_fields=["status", "error_detail", "updated_at"])
        log.warning("extraction skipped: %s", exc)


# ---------------------------------------------------------------------------
# Quote export (FR-10, bottleneck B14)
# ---------------------------------------------------------------------------

def handle_quote_export(payload: dict) -> None:
    """Render an approved quote off the request thread."""
    from common import mail
    from django.utils import timezone
    from projects.storage_ops import put_derived
    from quotes.models import Quote
    from quotes.pdf_export import generate_quote_pdf

    from shared.enums import QuoteStatus
    from shared.s3_keys import get_quote_pdf_key

    quote = Quote.objects.select_related("project").get(id=payload["QuoteId"])
    if quote.status not in (QuoteStatus.APPROVED.value, QuoteStatus.EXPORTED.value):
        # Belt and braces: the endpoint already refuses, but a queued message must
        # not be able to export something that was never approved (NFR-1).
        log.error("refusing to export quote %s in status %s", quote.id, quote.status)
        return

    with timed("EXPORT", log):
        pdf_bytes = generate_quote_pdf(quote)
        key = get_quote_pdf_key(str(quote.project_id), str(quote.id), 1)
        put_derived(key, pdf_bytes, content_type="application/pdf")

    quote.export_key = key
    quote.exported_at = timezone.now()
    quote.status = QuoteStatus.EXPORTED.value
    quote.save(update_fields=["export_key", "exported_at", "status", "updated_at"])

    # FR-10: route the finished quote to whoever started the job. Until now this
    # recorded the recipient and logged it, which reads as delivery in every status
    # field and every log line without a message ever being sent.
    #
    # The PDF is already durable in S3 at this point, so a mail failure is logged
    # and the export stands rather than being rolled back — the artefact exists and
    # can be sent again, and losing it to a transient SMTP error would be the worse
    # outcome. `mail.send` never raises.
    recipient = quote.exported_to_email or quote.project.initiator_email
    delivered = mail.send(
        subject=f"CBC quote for {quote.project.name}",
        body="\n".join([
            "The attached quote has been approved and is ready for review.",
            "",
            f"Project: {quote.project.name}",
            f"Quote:   {quote.id}",
            "",
            "This message was sent by CBC Copilot.",
            "",
        ]),
        to=recipient,
        attachment=(f"quote-{quote.id}.pdf", pdf_bytes, "application/pdf"),
    )

    log.info(
        "quote exported",
        extra={
            "quote_id": str(quote.id),
            "recipient": recipient,
            "key": key,
            "emailed": delivered,
        },
    )
