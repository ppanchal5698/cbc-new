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



class TableBudgetExceeded(RuntimeError):
    """More tables would be sent to the premium model than the guard allows."""


def _assert_within_table_budget(count: int, located: bool, ceiling: int) -> None:
    """
    Refuse before spending, the way preprocessing already refuses (§10.3 item 8).

    The locate pass degrades to "extract every table" when it fails, which is the
    right call for recall — a false positive costs one call, a false negative
    costs a missing opening (Risk R12). It is also completely unbounded: a
    95-table plan set becomes 95 premium-model calls, and SQS will redeliver.

    So the fallback needs the ceiling preprocessing already has. Quarantining
    beats extracting the first forty, because a silently partial read is exactly
    the omission NFR-2 forbids — the estimator would see openings and have no way
    to know the rest were never looked at.
    """
    if count <= ceiling:
        return

    why = (
        "the locate pass failed, so every table was queued for extraction"
        if not located
        else "this document genuinely carries that many schedule tables"
    )
    raise TableBudgetExceeded(
        f"{count} tables would be sent to the premium model, over the "
        f"{ceiling}-table guard — {why}. Nothing has been spent. Raise "
        f"MAX_EXTRACT_TABLES_PER_DOCUMENT deliberately, or check why the routing "
        f"table classified this many pages as schedules."
    )


def _cached_extractions(document, prompt_version: str) -> dict:
    """Answers already paid for on an earlier delivery, by table id."""
    from openings.models import TableExtraction

    return {
        row.table_id: row
        for row in TableExtraction.objects.filter(
            document=document, prompt_version=prompt_version
        )
    }


def _remember_extraction(document, table_id, prompt_version, model_id, payload, response) -> None:
    """
    Record one table's answer so a redelivery reuses it.

    ``update_or_create`` rather than ``create``: two workers racing the same
    redelivered message would otherwise collide on the unique constraint and turn
    a cost optimisation into an outage.
    """
    from openings.models import TableExtraction

    TableExtraction.objects.update_or_create(
        document=document,
        table_id=table_id,
        prompt_version=prompt_version,
        defaults={
            "model_id": model_id,
            "payload": payload,
            "input_tokens": getattr(response, "input_tokens", 0) or 0,
            "output_tokens": getattr(response, "output_tokens", 0) or 0,
            "cached_input_tokens": getattr(response, "cache_read_tokens", 0) or 0,
        },
    )


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
            "hardware_prompt_sha": extract_stage.prompt_fingerprint(
                "hardware", settings_obj.hardware_prompt_version
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
            classifications, located = extract_stage.locate_tables_with_status(
                batches, model_id=cheap, version=settings_obj.locate_prompt_version
            )
            extractable = [
                b for b in batches
                if classifications.get(b.table_id) in extract_stage.EXTRACTABLE
            ]
            log.info(
                "pass A complete",
                extra={
                    "tables_total": len(batches),
                    "tables_extractable": len(extractable),
                    "locate_succeeded": located,
                },
            )

            _assert_within_table_budget(
                len(extractable), located, settings_obj.max_extract_tables_per_document
            )

            prompt_version = settings_obj.extraction_prompt_version
            cache = _cached_extractions(document, prompt_version)

            records: list[tuple[dict, dict]] = []
            for batch in extractable:
                cached = cache.get(batch.table_id)
                if cached is not None:
                    # Already answered on an earlier delivery. Reusing it is the
                    # whole point of B8: a retry storm cannot double-bill.
                    openings = cached.payload
                    cached_in += cached.cached_input_tokens
                else:
                    openings, response = extract_stage.extract_table(
                        batch, model_id=premium, version=prompt_version
                    )
                    _remember_extraction(
                        document, batch.table_id, prompt_version, premium, openings, response
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
            # One query for every element the model was shown, rather than one per
            # citation: a 40-opening schedule cites hundreds (bottleneck B11).
            #
            # Every batch, not just the extractable ones — the hardware-definition
            # tables are cited by §5.11 resolution below, and a component whose
            # cited element is missing from this map gets a provenance row with no
            # page and no bbox, which is a citation the viewer cannot show.
            all_ids = {eid for batch in batches for eid in batch.elements}
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
            # §5.11, still inside LINK because it is the same contract: cite a
            # real element, ground the value in it, or be rejected.
            _resolve_hardware(
                document=document,
                run_row=run_row,
                batches=batches,
                classifications=classifications,
                element_rows=element_rows,
                stats=stats,
                model_id=premium,
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


#: What Pass A must call a table for it to be a hardware-set definition.
HARDWARE_DEFINITION = {"HARDWARE_SCHEDULE"}


def _resolve_hardware(
    *, document, run_row, batches, classifications, element_rows, stats, model_id
) -> None:
    """
    Resolve the door schedule's hardware-group callouts to component lists (§5.11).

    A door schedule says ``HW-3``; the Division 08 spec section defines what
    ``HW-3`` contains. Joining them is a separate call with its own narrow context
    — a different task with different failure modes, and mixing it into opening
    extraction degrades both.

    Runs after the openings are persisted because the callouts *are* the input:
    resolving every set the document happens to define would pay for hardware no
    opening on this bid asks for.

    A callout whose definition is not in the document comes back unresolved and is
    persisted as a flagged row. It is never filled in from what such a set usually
    contains — a hardware set invented from the model's general knowledge is
    precisely the failure NFR-2 prohibits.
    """
    from openings.models import Opening

    callouts = {
        group
        for group in Opening.objects.filter(extraction_run=run_row)
        .exclude(hardware_group__isnull=True)
        .exclude(hardware_group="")
        .values_list("hardware_group", flat=True)
    }
    if not callouts:
        return

    definitions = [
        batch for batch in batches
        if classifications.get(batch.table_id) in HARDWARE_DEFINITION
    ]
    if not definitions:
        # No definition block anywhere in the document. Every callout is
        # unresolved, and saying so is the answer — not a reason to skip.
        for group in sorted(callouts):
            link_stage.persist_hardware_set(
                project=document.project,
                extraction_run=run_row,
                hardware_group=group,
                resolved=False,
                explicit_part=False,
                components=[],
                stats=stats,
            )
        log.info(
            "no hardware-set definition table found; %d callout(s) recorded unresolved",
            len(callouts),
        )
        return

    resolved = extract_stage.resolve_hardware_sets(
        definitions, callouts, model_id=model_id, version=get_settings().hardware_prompt_version
    )
    if resolved is None:
        return
    response, supplied = resolved

    seen = set()
    for entry in response.payload.get("sets", []):
        group = entry.get("hardware_group")
        if not group:
            continue
        seen.add(group)
        components = [
            link_stage.link_component(
                component,
                supplied_elements=supplied,
                element_rows=element_rows,
                stats=stats,
            )
            for component in entry.get("components", [])
        ]
        link_stage.persist_hardware_set(
            project=document.project,
            extraction_run=run_row,
            hardware_group=group,
            resolved=bool(entry.get("resolved")),
            explicit_part=bool(entry.get("explicit_part")),
            components=components,
            stats=stats,
        )

    # A callout the model did not mention has not been ruled out — it has been
    # forgotten. Same reasoning as the locate pass: silence is not an answer.
    for group in sorted(callouts - seen):
        link_stage.persist_hardware_set(
            project=document.project,
            extraction_run=run_row,
            hardware_group=group,
            resolved=False,
            explicit_part=False,
            components=[],
            stats=stats,
        )


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
    except Exception as exc:
        repo.fail_job(job, str(exc))
        raise

    _price(document)
    return counts


def _price(document) -> None:
    """
    PRICE — build the draft quote and total it (§3.3 step 11, §6.2, FR-7).

    Deterministic, and there is no LLM call anywhere in this path. It runs here
    rather than waiting for the estimator to ask because the promise in NFR-6 is
    a *reviewable draft* in minutes — a grid of matched openings is a finding
    list, not a draft, and re-keying it into a quote by hand is the manual work
    this system exists to remove.

    A project that already has a draft with generated lines is left alone: a
    second document on the same bid (an addendum, a separate hardware spec) must
    not rebuild over an estimator's edits. Regenerating is an explicit action on
    the quote endpoint.
    """
    from quotes.draft_ops import DraftError, generate_lines
    from quotes.models import Quote

    from shared.enums import QuoteStatus

    job = repo.get_or_create_job(document, PipelineStage.PRICE)
    repo.start_job(job)
    try:
        with timed("PRICE", log):
            quote, was_created = Quote.objects.get_or_create(
                project=document.project,
                status=QuoteStatus.DRAFT.value,
                defaults={"tax_jurisdiction": None},
            )
            try:
                generate_lines(quote)
            except DraftError as exc:
                # Not a failure: an existing draft with lines is the normal
                # outcome for the second document on a bid.
                log.info("draft quote left as it stands: %s", exc)
                repo.complete_job(job)
                return

        log.info(
            "draft quote ready",
            extra={
                "quote_id": str(quote.id),
                # Not "created": logging reserves that attribute on LogRecord and
                # raises rather than shadowing it.
                "quote_created": was_created,
                "grand_total": str(quote.grand_total),
            },
        )
        repo.complete_job(job)
    except Exception as exc:
        repo.fail_job(job, str(exc))
        raise
