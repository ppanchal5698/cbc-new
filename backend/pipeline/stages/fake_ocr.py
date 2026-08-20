"""
Offline OCR replay (§8.3).

    Textract and Bedrock are the only services stubbed; a ``FAKE_OCR=1`` mode
    replays a recorded OCR JSON so the whole pipeline runs with no AWS calls and
    no spend.

Rather than shipping a recorded fixture that only matches one document, this
synthesises Textract-shaped blocks from the PDF's own text layer with PyMuPDF.
The result works on **any** document, which is what makes the offline loop useful
for development rather than a single-case demo.

What it reproduces faithfully:

* Block types, 0-1 polygon geometry, and page numbers — so normalisation,
  ``element_path`` construction, and the viewer overlay are exercised for real.
* Table structure from PyMuPDF's own table finder, including row and column
  indices, so table-scoped extraction batching (§5.3) has real tables to scope to.

What it cannot reproduce, and must not be trusted for:

* **Recognition accuracy.** There is no recognition here; the text is read from
  the file. ``Confidence`` is therefore omitted, and normalisation stores a null
  ``ocr_confidence`` rather than a fabricated 1.0 — a fake confidence would let a
  composite score claim certainty no measurement supports (§5.9).
* Scanned or vector-outlined pages, which have no text layer to read. They yield
  no blocks, which is the honest answer.
"""

from __future__ import annotations

import logging

import pymupdf

from shared.enums import OCRRoute

log = logging.getLogger("cbc.fake_ocr")


def _polygon(rect: pymupdf.Rect, width: float, height: float) -> list[dict]:
    """PyMuPDF points to Textract's 0-1 page fractions, clockwise from top-left."""
    x0, y0 = rect.x0 / width, rect.y0 / height
    x1, y1 = rect.x1 / width, rect.y1 / height
    return [{"X": x0, "Y": y0}, {"X": x1, "Y": y0}, {"X": x1, "Y": y1}, {"X": x0, "Y": y1}]


def _geometry(rect: pymupdf.Rect, width: float, height: float) -> dict:
    return {
        "BoundingBox": {
            "Left": rect.x0 / width,
            "Top": rect.y0 / height,
            "Width": (rect.x1 - rect.x0) / width,
            "Height": (rect.y1 - rect.y0) / height,
        },
        "Polygon": _polygon(rect, width, height),
    }


def synthesize_page(page: pymupdf.Page, page_number: int, *, with_tables: bool) -> list[dict]:
    """Build Textract-shaped blocks for one page."""
    blocks: list[dict] = []
    width, height = page.rect.width, page.rect.height
    if not width or not height:
        return blocks

    counter = 0

    def next_id(prefix: str) -> str:
        nonlocal counter
        counter += 1
        return f"fake-{page_number}-{prefix}-{counter}"

    if with_tables:
        try:
            found = page.find_tables()
        except Exception:  # noqa: BLE001 - table finding is best-effort
            found = None
        for table_index, table in enumerate(getattr(found, "tables", []) or []):
            cell_ids: list[str] = []
            cell_blocks: list[dict] = []
            for row_index, row in enumerate(table.extract()):
                for col_index, cell_text in enumerate(row):
                    cell_id = next_id("cell")
                    cell_ids.append(cell_id)
                    cell_blocks.append(
                        {
                            "BlockType": "CELL",
                            "Id": cell_id,
                            "Page": page_number,
                            "Text": (cell_text or "").strip(),
                            # Textract is 1-indexed; normalisation converts down.
                            "RowIndex": row_index + 1,
                            "ColumnIndex": col_index + 1,
                            "EntityTypes": ["COLUMN_HEADER"] if row_index == 0 else [],
                            "Geometry": _geometry(pymupdf.Rect(table.bbox), width, height),
                        }
                    )
            blocks.append(
                {
                    "BlockType": "TABLE",
                    "Id": next_id(f"table{table_index}"),
                    "Page": page_number,
                    "Geometry": _geometry(pymupdf.Rect(table.bbox), width, height),
                    "Relationships": [{"Type": "CHILD", "Ids": cell_ids}],
                }
            )
            blocks.extend(cell_blocks)

    for word in page.get_text("words"):
        x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
        blocks.append(
            {
                "BlockType": "WORD",
                "Id": next_id("word"),
                "Page": page_number,
                "Text": text,
                # No Confidence key: nothing was recognised, so there is nothing to
                # be confident about. Normalisation stores null.
                "Geometry": _geometry(pymupdf.Rect(x0, y0, x1, y1), width, height),
            }
        )

    return blocks


def synthesize(file_bytes: bytes, probes) -> dict:
    """
    Build a Textract-shaped result for the pages triage actually routed to OCR.

    Only routed pages are synthesised. Producing blocks for skipped pages would
    make the offline loop behave *better* than production, hiding exactly the
    triage mistakes Risk R12 asks us to watch for.
    """
    ocr_routes = {OCRRoute.TEXTRACT_TABLES.value, OCRRoute.TEXTRACT_TEXT.value}
    routed = {p.page_number: p.ocr_route for p in probes if p.ocr_route in ocr_routes}

    blocks: list[dict] = []
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        for page_number, route in sorted(routed.items()):
            page = doc[page_number - 1]
            blocks.append(
                {
                    "BlockType": "PAGE",
                    "Id": f"fake-page-{page_number}",
                    "Page": page_number,
                    "Geometry": _geometry(page.rect, page.rect.width, page.rect.height),
                }
            )
            blocks.extend(
                synthesize_page(
                    page, page_number, with_tables=(route == OCRRoute.TEXTRACT_TABLES.value)
                )
            )

    log.warning(
        "FAKE_OCR active — no Textract call was made and no money was spent. "
        "Confidences are null because nothing was recognised.",
        extra={"pages": len(routed), "blocks": len(blocks)},
    )
    return {
        "JobId": "FAKE_OCR",
        "Blocks": blocks,
        "DocumentMetadata": {"Pages": len(routed)},
        "_fake": True,
    }
