"""
Normalisation — Textract blocks to ``doc_elements`` (§3.3 step 7, §7.2).

Two things here are load-bearing.

**1. ``element_path`` is positional, never ``Block.Id``.** Textract mints a fresh
``Block.Id`` on every job. Keying elements by it means a re-run produces a
completely different set of identities, orphaning every citation an estimator has
already reviewed and silently breaking the Phase 1 exit criterion ("re-running
normalisation reproduces identical element identities"). A positional path —
``pages/3/words/412``, ``tables/0/cells/17`` — is stable across runs, which is
what makes normalisation idempotent.

**2. Page numbers are document-global.** A split part reports page 1; the manifest
knows that part started at global page 1001. The offset is applied here, before
the row is written, because a citation must always point at a page number that
means something in the PDF the estimator is looking at (§4.6).

Writes go through ``COPY``, not the ORM: tens of thousands of rows per bid set
inserted one at a time is bottleneck B3, and it is the largest single load spike
on a burstable database instance.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC

import psycopg

from shared.enums import ElementType

log = logging.getLogger("cbc.normalize")

#: Textract block types we keep. PAGE, TABLE, and LAYOUT_* blocks are structural
#: containers; their geometry is already implied by the cells and words inside.
TYPE_MAP = {
    "WORD": ElementType.WORD.value,
    "LINE": ElementType.LINE.value,
    "CELL": ElementType.TABLE_CELL.value,
    "MERGED_CELL": ElementType.TABLE_CELL.value,
    "SELECTION_ELEMENT": ElementType.SELECTION_MARK.value,
}

#: Column order for the COPY. Must match ELEMENT_COLUMNS exactly.
ELEMENT_COLUMNS = (
    "id", "document_id", "element_path", "page_number", "element_type", "text",
    "x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3",
    "bbox_x_min", "bbox_y_min", "bbox_x_max", "bbox_y_max",
    "ocr_confidence", "reading_order", "table_id", "row_index", "col_index",
    "column_header", "created_at", "updated_at",
)

COPY_SQL = f"COPY openings_docelement ({', '.join(ELEMENT_COLUMNS)}) FROM STDIN"

#: Rows per COPY flush. Large enough that the round-trip cost disappears, small
#: enough that a failure does not roll back an entire 200-page set (§9 B3).
BATCH_SIZE = 8000


@dataclass
class NormalisedElement:
    """One ``doc_elements`` row, before it reaches the database."""

    element_path: str
    page_number: int
    element_type: str
    text: str
    polygon: tuple[float, ...]
    bbox: tuple[float, float, float, float]
    ocr_confidence: float | None
    reading_order: int
    table_id: uuid.UUID | None = None
    row_index: int | None = None
    col_index: int | None = None
    column_header: bool | None = None


def _polygon(geometry: dict) -> tuple[float, ...]:
    """Four vertices as a flat 8-tuple of 0-1 page fractions."""
    points = geometry.get("Polygon", [])[:4]
    flat: list[float] = []
    for point in points:
        flat.extend([float(point.get("X", 0.0)), float(point.get("Y", 0.0))])
    while len(flat) < 8:
        flat.append(0.0)
    return tuple(flat)


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    box = geometry.get("BoundingBox", {})
    left = float(box.get("Left", 0.0))
    top = float(box.get("Top", 0.0))
    return left, top, left + float(box.get("Width", 0.0)), top + float(box.get("Height", 0.0))


def parse_blocks(
    blocks: list[dict], *, page_offset: int = 0, part_index: int = 0
) -> list[NormalisedElement]:
    """
    Flatten Textract blocks into stable, positionally-keyed elements.

    ``page_offset`` converts part-local page numbers back to document-global ones.
    ``part_index`` is *not* part of ``element_path``: the path is built from the
    global page index so that re-running after a different split still produces
    identical element identities.
    """
    # Per-page, per-type counters give each element its positional index.
    counters: dict[tuple[int, str], int] = {}
    # Textract identifies tables by Block.Id, which is unstable across runs; map it
    # to a per-page ordinal so table_id groupings survive a re-run too.
    table_ordinals: dict[str, int] = {}
    table_uuids: dict[str, uuid.UUID] = {}
    reading_order = 0
    elements: list[NormalisedElement] = []

    # First pass: assign each TABLE block a deterministic per-page ordinal.
    for block in blocks:
        if block.get("BlockType") == "TABLE":
            page = int(block.get("Page", 1)) + page_offset
            ordinal = sum(1 for key in table_ordinals.values() if key == page)
            table_ordinals[block["Id"]] = page
            table_uuids[block["Id"]] = uuid.uuid5(
                uuid.NAMESPACE_URL, f"table/{page}/{ordinal}"
            )

    # Map each cell to its parent table via the TABLE block's CHILD relationships.
    cell_to_table: dict[str, str] = {}
    for block in blocks:
        if block.get("BlockType") != "TABLE":
            continue
        for relationship in block.get("Relationships", []):
            if relationship.get("Type") == "CHILD":
                for child_id in relationship.get("Ids", []):
                    cell_to_table[child_id] = block["Id"]

    for block in blocks:
        block_type = block.get("BlockType")
        element_type = TYPE_MAP.get(block_type)
        if element_type is None:
            continue

        page_number = int(block.get("Page", 1)) + page_offset
        key = (page_number, element_type)
        index = counters.get(key, 0)
        counters[key] = index + 1

        parent_table = cell_to_table.get(block.get("Id", ""))
        if element_type == ElementType.TABLE_CELL.value and parent_table:
            # tables/<n>/cells/<i> — the shape §7.2 names explicitly.
            table_page = table_ordinals.get(parent_table, page_number)
            table_ordinal = sorted(
                tid for tid, pg in table_ordinals.items() if pg == table_page
            ).index(parent_table)
            element_path = f"pages/{page_number}/tables/{table_ordinal}/cells/{index}"
        else:
            element_path = f"pages/{page_number}/{element_type}s/{index}"

        geometry = block.get("Geometry", {})
        confidence = block.get("Confidence")

        elements.append(
            NormalisedElement(
                element_path=element_path,
                page_number=page_number,
                element_type=element_type,
                text=block.get("Text", "") or "",
                polygon=_polygon(geometry),
                bbox=_bbox(geometry),
                # Textract reports 0-100; stored 0-1 and NEVER recomputed after.
                ocr_confidence=(float(confidence) / 100.0) if confidence is not None else None,
                reading_order=reading_order,
                table_id=table_uuids.get(parent_table) if parent_table else None,
                row_index=(block.get("RowIndex") - 1) if block.get("RowIndex") else None,
                col_index=(block.get("ColumnIndex") - 1) if block.get("ColumnIndex") else None,
                column_header="COLUMN_HEADER" in (block.get("EntityTypes") or []),
            )
        )
        reading_order += 1

    return elements


def parse_native_text(page_number: int, words: list[tuple]) -> list[NormalisedElement]:
    """
    Build elements from PyMuPDF's native text layer — the zero-cost OCR path.

    ``words`` is PyMuPDF's ``(x0, y0, x1, y1, word, block, line, word_no)`` tuples
    in *points*, so they are converted to 0-1 fractions to match Textract's
    geometry. ``ocr_confidence`` is None rather than 1.0: the text was read from
    the file, not recognised, and claiming perfect OCR confidence would let a
    composite confidence be higher than any measurement supports (§5.9).
    """
    elements: list[NormalisedElement] = []
    for index, word in enumerate(words):
        x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
        elements.append(
            NormalisedElement(
                element_path=f"pages/{page_number}/words/{index}",
                page_number=page_number,
                element_type=ElementType.WORD.value,
                text=text,
                polygon=(x0, y0, x1, y0, x1, y1, x0, y1),
                bbox=(x0, y0, x1, y1),
                ocr_confidence=None,
                reading_order=index,
            )
        )
    return elements


def scale_native_elements(
    elements: list[NormalisedElement], width_pt: float, height_pt: float
) -> list[NormalisedElement]:
    """Convert point coordinates to the 0-1 fractions the viewer overlay expects."""
    if not width_pt or not height_pt:
        return elements
    scaled = []
    for element in elements:
        poly = tuple(
            value / (width_pt if i % 2 == 0 else height_pt)
            for i, value in enumerate(element.polygon)
        )
        x0, y0, x1, y1 = element.bbox
        element.polygon = poly
        element.bbox = (x0 / width_pt, y0 / height_pt, x1 / width_pt, y1 / height_pt)
        scaled.append(element)
    return scaled


def bulk_insert_elements(dsn: str, document_id: str, elements: list[NormalisedElement]) -> int:
    """
    Write elements with ``COPY``, idempotently, in one transaction per document.

    Idempotency is achieved by deleting this document's existing elements first
    rather than by ``ON CONFLICT``: ``COPY`` has no upsert, and because
    ``element_path`` is positional a re-run reproduces exactly the same paths, so
    delete-then-copy converges on an identical set.

    ``ON DELETE RESTRICT`` on ``field_provenance_elements`` means this delete
    *fails* if a live citation points at any of these elements. That is deliberate:
    re-normalising underneath a reviewed extraction would silently invalidate the
    estimator's work, and the database refusing is the correct outcome.
    """
    if not elements:
        return 0

    from datetime import datetime

    now = datetime.now(UTC)
    written = 0

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM openings_docelement WHERE document_id = %s", (document_id,)
            )
            with cursor.copy(COPY_SQL) as copy:
                for element in elements:
                    copy.write_row(
                        (
                            str(uuid.uuid4()),
                            document_id,
                            element.element_path,
                            element.page_number,
                            element.element_type,
                            element.text,
                            *element.polygon,
                            *element.bbox,
                            element.ocr_confidence,
                            element.reading_order,
                            str(element.table_id) if element.table_id else None,
                            element.row_index,
                            element.col_index,
                            element.column_header,
                            now,
                            now,
                        )
                    )
                    written += 1
        connection.commit()

    log.info(
        "elements normalised",
        extra={"document_id": document_id, "elements": written},
    )
    return written
