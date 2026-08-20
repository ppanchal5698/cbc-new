"""
Database writes for the pipeline worker.

The worker uses the **Django ORM** for row-at-a-time writes (jobs, manifest rows,
openings) and **psycopg COPY** for the one bulk path that matters,
``doc_elements`` (bottleneck B3).

It never runs a migration. Django owns the schema (§3.2 rule 1, ADR-0001); the
worker reads and writes Django-migrated tables and ``test_schema_parity.py``
fails CI if the two ever disagree.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from shared.enums import DocumentStatus, PipelineJobStatus, PipelineStage

log = logging.getLogger("cbc.repository")


# ---------------------------------------------------------------------------
# Pipeline jobs (§7.7)
# ---------------------------------------------------------------------------

def get_or_create_job(document, stage: PipelineStage, *, idempotency_key: str | None = None):
    from projects.models import PipelineJob

    job, created = PipelineJob.objects.get_or_create(
        document=document,
        stage=stage.value,
        defaults={
            "project": document.project,
            "status": PipelineJobStatus.PENDING.value,
            "idempotency_key": idempotency_key,
        },
    )
    if not created and idempotency_key and not job.idempotency_key:
        job.idempotency_key = idempotency_key
        job.save(update_fields=["idempotency_key", "updated_at"])
    return job


def start_job(job):
    """Mark a job started and count the attempt."""
    job.status = PipelineJobStatus.STARTED.value
    job.attempt += 1
    job.started_at = datetime.now(UTC)
    job.error_detail = ""
    job.save(update_fields=["status", "attempt", "started_at", "error_detail", "updated_at"])
    return job


def complete_job(job, *, cost_actual: Decimal | None = None):
    job.status = PipelineJobStatus.COMPLETED.value
    job.completed_at = datetime.now(UTC)
    if cost_actual is not None:
        job.cost_actual = cost_actual
    job.save(update_fields=["status", "completed_at", "cost_actual", "updated_at"])
    return job


def fail_job(job, error: str, *, quarantine: bool = False):
    """
    Record a failure.

    ``quarantine=True`` is the DLQ landing state (bottleneck B7): after
    ``maxReceiveCount`` deliveries the message is off the queue, and the job row
    must say QUARANTINED rather than sitting at FAILED as though a retry were
    still coming.
    """
    job.status = (
        PipelineJobStatus.QUARANTINED.value if quarantine else PipelineJobStatus.FAILED.value
    )
    job.error_detail = str(error)[:8000]
    job.completed_at = datetime.now(UTC)
    job.save(update_fields=["status", "error_detail", "completed_at", "updated_at"])
    return job


def record_external_job_id(job, external_job_id: str):
    """
    Persist the Textract JobId **before the call is considered complete** (B8).

    This is the whole redelivery guard: on redelivery the consumer reads this
    field, sees work already in flight, and resumes instead of paying for a second
    submission.
    """
    job.external_job_id = external_job_id
    job.save(update_fields=["external_job_id", "updated_at"])
    return job


# ---------------------------------------------------------------------------
# Manifest (§4.1)
# ---------------------------------------------------------------------------

def write_manifest(document, probes, *, raster_keys: dict | None = None) -> int:
    """
    Persist one manifest row per page, **before any OCR call**.

    Replaces the document's existing rows so a re-preprocess converges rather than
    accumulating duplicates. ``forced_by_user`` survives: an estimator's decision
    to read page 47 must not be undone by a reprocess (Risk R12).
    """
    from django.db import transaction
    from projects.models import DocumentManifest

    raster_keys = raster_keys or {}

    with transaction.atomic():
        forced = {
            row.page_number: (row.ocr_route, row.route_reason, row.forced_by_user_id)
            for row in DocumentManifest.objects.filter(
                document=document, forced_by_user__isnull=False
            )
        }
        DocumentManifest.objects.filter(document=document).delete()

        rows = []
        for probe in probes:
            keys = raster_keys.get(probe.page_number, {})
            route, reason, forced_by = forced.get(
                probe.page_number, (probe.ocr_route, probe.route_reason, None)
            )
            rows.append(
                DocumentManifest(
                    document=document,
                    page_number=probe.page_number,
                    page_hash=probe.page_hash,
                    width_pt=probe.width_pt,
                    height_pt=probe.height_pt,
                    rotation=probe.rotation,
                    text_layer=probe.text_layer,
                    native_word_count=probe.native_word_count,
                    vector_path_count=probe.vector_path_count,
                    page_class=probe.page_class,
                    class_confidence=Decimal(str(probe.class_confidence)),
                    class_method=probe.class_method,
                    ocr_route=route,
                    route_reason=reason,
                    forced_by_user_id=forced_by,
                    raster_key=keys.get("viewer"),
                    thumb_key=keys.get("thumb"),
                    ocr_input_key=keys.get("ocr-input"),
                    ocr_cost_estimate=probe.ocr_cost_estimate,
                    split_part=probe.split_part,
                    page_offset=probe.page_offset,
                )
            )
        DocumentManifest.objects.bulk_create(rows, batch_size=500)

        document.page_count = len(rows)
        document.manifest_complete = True
        document.estimated_ocr_cost_usd = sum(
            (p.ocr_cost_estimate for p in probes), Decimal("0")
        )
        document.save(
            update_fields=[
                "page_count", "manifest_complete", "estimated_ocr_cost_usd", "updated_at",
            ]
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Addendum diffing (§4.7)
# ---------------------------------------------------------------------------

def write_page_diffs(document, previous_document) -> dict:
    """
    Compare page hashes against an earlier document and record the differences.

    Unchanged pages reuse their existing elements and extraction — no OCR call, no
    LLM call, no cost (bottleneck B13). Changed pages are reprocessed and their
    openings marked for re-review.

    **Diff report only.** No reconciliation logic: CBC has not said whether an
    addendum is a new document, a revision to an existing version, or both, and
    carrying an assumption forward unexamined is the silent-assumption failure mode
    Risk R2 names.
    """
    from projects.models import DocumentManifest, PageDiff

    from shared.enums import PageDiffStatus

    current = {
        row.page_number: row.page_hash
        for row in DocumentManifest.objects.filter(document=document)
    }
    previous = {
        row.page_number: row.page_hash
        for row in DocumentManifest.objects.filter(document=previous_document)
    }

    PageDiff.objects.filter(document=document, compared_to_document=previous_document).delete()

    diffs, counts = [], {status.value: 0 for status in PageDiffStatus}
    for page_number in sorted(set(current) | set(previous)):
        if page_number not in previous:
            status = PageDiffStatus.ADDED
        elif page_number not in current:
            status = PageDiffStatus.REMOVED
        elif current[page_number] == previous[page_number]:
            status = PageDiffStatus.UNCHANGED
        else:
            status = PageDiffStatus.CHANGED
        counts[status.value] += 1
        diffs.append(
            PageDiff(
                document=document,
                compared_to_document=previous_document,
                page_number=page_number,
                status=status.value,
            )
        )
    PageDiff.objects.bulk_create(diffs, batch_size=500)
    log.info("page diff written", extra={"document_id": str(document.id), **counts})
    return counts


def unchanged_pages(document, previous_document) -> set[int]:
    """Pages whose content is byte-identical to the earlier document's."""
    from projects.models import PageDiff

    from shared.enums import PageDiffStatus

    return set(
        PageDiff.objects.filter(
            document=document,
            compared_to_document=previous_document,
            status=PageDiffStatus.UNCHANGED.value,
        ).values_list("page_number", flat=True)
    )


# ---------------------------------------------------------------------------
# Document status
# ---------------------------------------------------------------------------

def set_document_status(document, status: DocumentStatus, detail: str = ""):
    document.status = status.value
    document.status_detail = detail[:4000]
    document.save(update_fields=["status", "status_detail", "updated_at"])
    return document
