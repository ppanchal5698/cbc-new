"""
EXTRACT + LINK orchestration (§3.3 steps 8-9).

Kept separate from :mod:`pipeline.coordinator` because the two stages share an
``extraction_runs`` row, a model pair, and a statistics accumulator — threading
all of that through the message dispatcher would put pipeline bookkeeping in the
same function as SQS routing.

The extraction run row is written **first**, before any model call, so a crash
mid-extraction still leaves a record of which model and prompt version were in
force (NFR-3).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pipeline.db import repository as repo
from pipeline.observability.logging_setup import timed
from pipeline.routing import load_routing_table
from pipeline.stages import extract as extract_stage
from pipeline.stages import link as link_stage
from shared.config import get_settings
from shared.enums import ExtractionRunStatus, PipelineStage

log = logging.getLogger("cbc.extraction")


def run(document) -> dict:
    """
    Two-pass extraction over one document, then validation and persistence.

    Returns the statistics dict for ``extraction_metrics``.
    """
    from feedback.models import ExtractionMetric
    from openings.models import DocElement, ExtractionRun

    settings_obj = get_settings()
    premium, cheap = settings_obj.require_bedrock()
    table = load_routing_table()

    run_row = ExtractionRun.objects.create(
        document=document,
        model_id=premium,
        model_id_cheap=cheap,
        prompt_version=settings_obj.extraction_prompt_version,
        inference_params={
            "temperature": settings_obj.bedrock_temperature,
            # top_p is deliberately absent: bedrock.invoke does not send it, and
            # recording a parameter the request never carried would make the
            # audit trail claim something untrue about how a value was produced.
            "max_tokens": settings_obj.bedrock_max_tokens,
            # Fingerprints, so a silently edited prompt file is detectable after
            # the fact even though §8.2 forbids editing one in place.
            "extraction_prompt_sha": extract_stage.prompt_fingerprint(
                "extraction", settings_obj.extraction_prompt_version
            ),
            "locate_prompt_sha": extract_stage.prompt_fingerprint(
                "locate", settings_obj.locate_prompt_version
            ),
        },
        ocr_result_version_id=document.ocr_result_version_id,
        route_config_version=table.content_hash,
        status=ExtractionRunStatus.STARTED.value,
    )

    extract_job = repo.get_or_create_job(document, PipelineStage.EXTRACT)
    repo.start_job(extract_job)
    stats = link_stage.LinkStats()
    tokens_in = tokens_out = cached_in = 0

    try:
        batches = extract_stage.build_table_batches(str(document.id))
        if not batches:
            log.warning("no tables found; nothing to extract")
            repo.complete_job(extract_job)
            run_row.status = ExtractionRunStatus.COMPLETED.value
            run_row.completed_at = datetime.now(UTC)
            run_row.save()
            return stats.as_dict()

        with timed("EXTRACT", log):
            classifications = extract_stage.locate_tables(
                batches, model_id=cheap, version=settings_obj.locate_prompt_version
            )
            extractable = [
                b for b in batches
                if classifications.get(b.table_id) in extract_stage.EXTRACTABLE
            ]
            log.info(
                "pass A complete",
                extra={"tables_total": len(batches), "tables_extractable": len(extractable)},
            )

            records: list[tuple[dict, dict]] = []
            for batch in extractable:
                openings, response = extract_stage.extract_table(
                    batch, model_id=premium, version=settings_obj.extraction_prompt_version
                )
                if response is not None:
                    tokens_in += response.input_tokens
                    tokens_out += response.output_tokens
                    cached_in += response.cache_read_tokens
                for record in openings:
                    records.append((record, batch.elements))

        link_job = repo.get_or_create_job(document, PipelineStage.LINK)
        repo.start_job(link_job)
        with timed("LINK", log):
            # One query for every cited element, rather than one per citation:
            # a 40-opening schedule cites hundreds of elements (bottleneck B11).
            all_ids = {eid for _, elements in records for eid in elements}
            element_rows = {
                str(row.id): row
                for row in DocElement.objects.filter(id__in=all_ids)
            }

            for record, supplied in records:
                linked = link_stage.link_opening(
                    record,
                    supplied_elements=supplied,
                    element_rows=element_rows,
                    stats=stats,
                )
                link_stage.persist_opening(
                    project=document.project,
                    extraction_run=run_row,
                    record=record,
                    linked=linked,
                    stats=stats,
                )
        repo.complete_job(link_job)
        repo.complete_job(extract_job)

        run_row.status = ExtractionRunStatus.COMPLETED.value
        run_row.input_tokens = tokens_in
        run_row.output_tokens = tokens_out
        run_row.cached_input_tokens = cached_in
        run_row.completed_at = datetime.now(UTC)
        run_row.save()

        ExtractionMetric.objects.update_or_create(
            extraction_run=run_row, defaults=stats.as_dict()
        )

        _match(document)
        log.info(
            "extraction complete",
            extra={
                "openings": stats.openings_written,
                "accepted": stats.fields_accepted,
                "rejected_citation": stats.fields_rejected_citation,
                "rejected_grounding": stats.fields_rejected_grounding,
            },
        )
        return stats.as_dict()

    except Exception as exc:
        run_row.status = ExtractionRunStatus.FAILED.value
        run_row.error_detail = str(exc)[:4000]
        run_row.completed_at = datetime.now(UTC)
        run_row.save()
        repo.fail_job(extract_job, str(exc))
        raise


def _match(document) -> dict:
    """
    MATCH — deterministic, no LLM (§6.1).

    Runs after LINK because it consumes the *typed* fields the deterministic
    parsers produced, not the raw strings the model returned. Matching on
    ``fire_rating_raw`` would mean comparing "90 MIN" to an integer.
    """
    from pipeline.stages import match as match_stage

    job = repo.get_or_create_job(document, PipelineStage.MATCH)
    repo.start_job(job)
    try:
        with timed("MATCH", log):
            counts = match_stage.match_project(document.project)
        repo.complete_job(job)
        return counts
    except Exception as exc:
        repo.fail_job(job, str(exc))
        raise
