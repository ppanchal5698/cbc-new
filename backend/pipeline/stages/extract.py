"""
Two-pass extraction (§5.3, §5.4, §5.12).

    Do not send a document to the model and ask for openings.

**Pass A — Locate (cheap tier).** Input is the table *inventory* from Textract for
classified schedule pages — table ids, dimensions, header-row text only. Output is
which tables are the door schedule, the frame schedule, the hardware-set
definitions, and which are irrelevant. Small context, cheap, and it prevents
Pass B from ever seeing a finish legend.

**Pass B — Extract (premium tier).** One call **per table**, not per document.
Input is that table's cells with their ``element_id``s, its header row, and a
bounded window of surrounding text elements. Output is structured opening records
with citations.

Batching per table is what keeps context small, cost predictable, and failures
isolated — one malformed table fails one call, not the whole bid set (bottleneck
B6).

**The model never receives raw pixels and never receives a whole document.**
"""

from __future__ import annotations

import functools
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.llm import bedrock
from pipeline.llm.schemas.extraction import (
    EXTRACTION_TOOL,
    HARDWARE_TOOL,
    LOCATE_TOOL,
    OPENING_FIELDS,
)
from shared.config import get_settings

log = logging.getLogger("cbc.extract")

PROMPT_ROOT = Path(__file__).resolve().parent.parent / "llm" / "prompts"

#: Cell text longer than this is truncated in the inventory sent to Pass A. The
#: locate pass judges structure, not content, and a 2,000-character general-note
#: cell would dominate the context for no benefit.
INVENTORY_TEXT_CAP = 120

#: How many non-table elements adjacent to a table are included as context in
#: Pass B. Notes and legends beside a schedule genuinely disambiguate it; the
#: whole page does not (bottleneck B6).
CONTEXT_WINDOW = 40


class ExtractionError(RuntimeError):
    """The extraction call failed in a way the caller must handle."""


@functools.lru_cache(maxsize=16)
def load_prompt(kind: str, version: str) -> str:
    """
    Read a versioned prompt file.

    Cached because the prefix must be **byte-identical** across calls for prompt
    caching to work at all (§5.12); re-reading risks a trailing-newline difference
    silently disabling the cache and tripling the input bill.
    """
    path = PROMPT_ROOT / kind / f"{version}.md"
    if not path.exists():
        raise ExtractionError(
            f"prompt {kind}/{version}.md does not exist. Prompts are versioned "
            f"artefacts and extraction_runs.prompt_version must resolve to an exact "
            f"file (§8.2) — a missing one means an unauditable run."
        )
    return path.read_text(encoding="utf-8")


def prompt_fingerprint(kind: str, version: str) -> str:
    """Content hash of a prompt, so a silent edit is detectable after the fact."""
    return hashlib.sha256(load_prompt(kind, version).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------

@dataclass
class TableBatch:
    """One table's cells plus a bounded context window. The unit of a Pass B call."""

    table_id: str
    page_number: int
    rows: int
    cols: int
    header_text: list[str] = field(default_factory=list)
    first_row_text: list[str] = field(default_factory=list)
    #: element_id -> text, exactly the set the model is shown. The validation gate
    #: checks citations against THIS, not against every element in the document:
    #: citing a real element the model was never shown is a hallucination that
    #: happens to land on something true.
    elements: dict[str, str] = field(default_factory=dict)
    cells: list[dict] = field(default_factory=list)
    context: list[dict] = field(default_factory=list)

    def inventory_entry(self) -> dict:
        return {
            "table_id": self.table_id,
            "page_number": self.page_number,
            "rows": self.rows,
            "columns": self.cols,
            "header_row": [t[:INVENTORY_TEXT_CAP] for t in self.header_text],
            "first_data_row": [t[:INVENTORY_TEXT_CAP] for t in self.first_row_text],
        }


def build_table_batches(document_id: str) -> list[TableBatch]:
    """
    Group this document's elements into per-table batches.

    Reads ``doc_elements`` rather than the OCR JSON: normalisation has already
    applied split-part offsets and assigned stable positional ``element_path``s, so
    a citation recorded here survives a re-run (§7.2).
    """
    from openings.models import DocElement

    cells = (
        DocElement.objects.filter(document_id=document_id, element_type="table_cell")
        .exclude(table_id=None)
        .order_by("page_number", "table_id", "row_index", "col_index")
    )

    batches: dict[str, TableBatch] = {}
    for cell in cells:
        key = str(cell.table_id)
        batch = batches.get(key)
        if batch is None:
            batch = TableBatch(table_id=key, page_number=cell.page_number, rows=0, cols=0)
            batches[key] = batch

        batch.rows = max(batch.rows, (cell.row_index or 0) + 1)
        batch.cols = max(batch.cols, (cell.col_index or 0) + 1)
        batch.elements[str(cell.id)] = cell.text
        batch.cells.append(
            {
                "element_id": str(cell.id),
                "text": cell.text,
                "row": cell.row_index,
                "column": cell.col_index,
                "is_header": bool(cell.column_header),
            }
        )
        if cell.row_index == 0:
            batch.header_text.append(cell.text)
        elif cell.row_index == 1:
            batch.first_row_text.append(cell.text)

    _attach_context(document_id, batches)
    log.info(
        "table batches built",
        extra={"document_id": document_id, "tables": len(batches)},
    )
    return list(batches.values())


def _attach_context(document_id: str, batches: dict[str, TableBatch]) -> None:
    """
    Add nearby non-table lines to each batch.

    A note beside a schedule ("ALL HM DOORS 90 MIN UNLESS NOTED") genuinely changes
    how a row reads, and §5.3 calls for "a bounded window of surrounding text
    elements adjacent to the table on the same page". Bounded is the operative
    word: the whole page would reintroduce bottleneck B6.
    """
    from openings.models import DocElement

    pages = {batch.page_number for batch in batches.values()}
    if not pages:
        return

    lines = DocElement.objects.filter(
        document_id=document_id, element_type="line", page_number__in=pages
    ).order_by("page_number", "reading_order")

    by_page: dict[int, list] = {}
    for line in lines:
        by_page.setdefault(line.page_number, []).append(line)

    for batch in batches.values():
        for line in (by_page.get(batch.page_number) or [])[:CONTEXT_WINDOW]:
            batch.elements[str(line.id)] = line.text
            batch.context.append({"element_id": str(line.id), "text": line.text})


# ---------------------------------------------------------------------------
# Pass A — locate
# ---------------------------------------------------------------------------

#: What Pass A must return for a table to be sent to Pass B.
EXTRACTABLE = {"DOOR_SCHEDULE", "FRAME_SCHEDULE"}


def locate_tables(batches: list[TableBatch], *, model_id: str, version: str) -> dict[str, str]:
    """
    Classify tables on the cheap tier. Returns ``{table_id: classification}``.

    A failure here is not fatal: falling back to "extract everything" costs more
    but loses nothing, whereas failing the document would lose the bid set over a
    classification call. Recall is weighted far above precision throughout triage
    and this is the same trade (Risk R12).
    """
    if not batches:
        return {}

    inventory = [batch.inventory_entry() for batch in batches]
    system, messages = bedrock.build_messages(
        cacheable_prefix=load_prompt("locate", version),
        variable_body=_as_json({"tables": inventory}),
    )

    try:
        response = bedrock.invoke(
            model_id=model_id,
            system=system,
            messages=messages,
            tool_spec=LOCATE_TOOL,
            tool_name=LOCATE_TOOL["name"],
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never drop the document
        log.warning("locate pass failed (%s); extracting every table instead", exc)
        return {batch.table_id: "DOOR_SCHEDULE" for batch in batches}

    result = {
        entry["table_id"]: entry["classification"]
        for entry in response.payload.get("tables", [])
        if entry.get("table_id")
    }
    # A table Pass A did not mention has not been ruled out — it has been
    # forgotten. Treat silence as "extract it" rather than as "irrelevant".
    for batch in batches:
        result.setdefault(batch.table_id, "DOOR_SCHEDULE")

    log.info(
        "locate pass complete",
        extra={
            "tables": len(result),
            "extractable": sum(1 for v in result.values() if v in EXTRACTABLE),
            "input_tokens": response.input_tokens,
            "cache_read_tokens": response.cache_read_tokens,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Pass B — extract
# ---------------------------------------------------------------------------

def extract_table(batch: TableBatch, *, model_id: str, version: str) -> tuple[list[dict], object]:
    """
    Extract openings from one table. Returns ``(opening_records, LLMResponse)``.

    Exactly one schema-repair retry is permitted, and **only** for output that
    fails structurally (the model answered in prose). Never for a semantic
    rejection, and never in a loop (§5.6).
    """
    system, messages = bedrock.build_messages(
        cacheable_prefix=load_prompt("extraction", version),
        variable_body=_as_json(
            {
                "table_id": batch.table_id,
                "page_number": batch.page_number,
                "cells": batch.cells,
                "adjacent_text": batch.context,
            }
        ),
    )

    for attempt in (1, 2):
        try:
            response = bedrock.invoke(
                model_id=model_id,
                system=system,
                messages=messages,
                tool_spec=EXTRACTION_TOOL,
                tool_name=EXTRACTION_TOOL["name"],
            )
            return response.payload.get("openings", []), response
        except bedrock.ToolNotCalled as exc:
            if attempt == 2:
                raise ExtractionError(
                    f"table {batch.table_id} produced unstructured output twice; "
                    f"refusing to parse prose (§5.4)"
                ) from exc
            log.warning("schema repair retry for table %s: %s", batch.table_id, exc)
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Your previous response did not call the tool. Respond "
                                "only by calling record_openings with the required "
                                "schema. Do not explain."
                            )
                        }
                    ],
                },
            ]

    return [], None  # unreachable; kept so the signature is total


def resolve_hardware_sets(
    batches: list[TableBatch], callouts: set[str], *, model_id: str, version: str
):
    """
    Cross-schedule resolution (§5.11).

    A separate call with its own narrow context: joining ``HW-3`` to its definition
    is a different task with different failure modes, and mixing it into opening
    extraction degrades both. Unresolved groups come back ``resolved=false`` and are
    flagged — never filled in from the model's general knowledge of what an
    ``HW-3`` usually contains.
    """
    if not callouts or not batches:
        return None

    elements: dict[str, str] = {}
    blocks: list[dict] = []
    for batch in batches:
        for cell in batch.cells:
            elements[cell["element_id"]] = cell["text"]
            blocks.append(cell)

    system, messages = bedrock.build_messages(
        cacheable_prefix=load_prompt("hardware", version),
        variable_body=_as_json(
            {"callouts": sorted(callouts), "definition_elements": blocks}
        ),
    )
    response = bedrock.invoke(
        model_id=model_id,
        system=system,
        messages=messages,
        tool_spec=HARDWARE_TOOL,
        tool_name=HARDWARE_TOOL["name"],
    )
    return response, elements


# ---------------------------------------------------------------------------

def _as_json(payload: dict) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def expected_field_count() -> int:
    """Denominator for the completeness penalty (§5.9)."""
    return len(OPENING_FIELDS)


def resolve_models() -> tuple[str, str]:
    """``(premium, cheap)`` model IDs, or an explanation of why they are missing."""
    return get_settings().require_bedrock()
