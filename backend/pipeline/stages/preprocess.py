"""
Document preprocessing — the money step (§4).

None of the five source documents contained a preprocessing stage; all went from
"upload PDF" straight to "call Textract on the document". For invoices that is
fine. For architectural bid sets it is the root cause of NFR-6 being unreachable
and of the largest single line on the AWS bill.

The shape of the problem: a bid set is **40-200+ pages** of drawings, of which
typically **3-8** contain the door, frame, and Division 08 hardware schedules.
Preprocessing answers one question per page — *does this page need structured OCR,
cheap OCR, or nothing?* — before spending a cent.

**Invariant:** the source PDF is read-only. Every output lands in ``derived/``,
never ``source/``. The manifest is persisted **before** the first OCR call,
because it is the audit answer to "why didn't the system read page 47?"
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal

import pikepdf
import pymupdf

from pipeline.routing import RoutingTable, load_routing_table, normalise_for_anchor
from shared.enums import ClassMethod, OCRRoute, PageClass, TextLayer

log = logging.getLogger("cbc.preprocess")

#: Title blocks sit in a predictable corner. Fraction of the page treated as the
#: bottom-right title-block region for Tier 2 (§4.3).
TITLE_BLOCK_FRACTION = 0.15

#: Sheet numbers look like A6.01, G0.1, A-601.
SHEET_NUMBER = re.compile(r"\b([A-Z]{1,2})[-]?(\d)[.\-]?(\d{1,2})?\b")


class PreprocessError(RuntimeError):
    """The document cannot be preprocessed and the job must fail with a reason."""


class EncryptedDocument(PreprocessError):
    """Password-protected. The estimator can supply the password or re-export."""


class BudgetExceeded(PreprocessError):
    """
    Estimated OCR cost exceeds ``MAX_OCR_COST_PER_DOCUMENT_USD``.

    The only control that catches "someone uploaded a 3,000-page set by mistake"
    *before* the money is gone (§10.3 item 8).
    """


@dataclass
class PageProbe:
    """Everything preprocessing knows about one page before any OCR spend."""

    page_number: int
    width_pt: float
    height_pt: float
    rotation: int
    native_word_count: int
    vector_path_count: int
    text_layer: str
    page_class: str
    class_confidence: float
    class_method: str | None
    ocr_route: str
    route_reason: str
    ocr_cost_estimate: Decimal
    page_hash: str
    split_part: int = 0
    page_offset: int = 0
    #: Native text, carried forward for NATIVE_TEXT pages so normalisation does
    #: not have to reopen the PDF.
    native_text: str = field(default="", repr=False)

    def to_manifest_row(self) -> dict:
        row = asdict(self)
        row.pop("native_text", None)
        return row


# ---------------------------------------------------------------------------
# Step 1 — validate and probe (§4.2)
# ---------------------------------------------------------------------------

def validate_pdf(data: bytes) -> tuple[bytes, bool]:
    """
    Check encryption and structural integrity before anything else.

    Returns ``(usable_bytes, was_repaired)``. A repaired copy is returned for the
    caller to write to **derived**; the source is never mutated (§4.2).
    """
    import io

    try:
        with pikepdf.open(io.BytesIO(data)):
            return data, False
    except pikepdf.PasswordError as exc:
        raise EncryptedDocument(
            "the PDF is password-protected. Supply the password or re-export it "
            "without encryption, then upload again."
        ) from exc
    except pikepdf.PdfError as exc:
        # Linearisation damage and truncated downloads are common and usually
        # recoverable. Repair into derived, proceed against the repaired copy, and
        # flag it so the estimator knows.
        log.warning("PDF failed to open cleanly; attempting repair: %s", exc)
        try:
            buffer = io.BytesIO()
            with pikepdf.open(io.BytesIO(data), allow_overwriting_input=False) as pdf:
                pdf.save(buffer)
            return buffer.getvalue(), True
        except Exception as repair_exc:  # noqa: BLE001 - any failure is terminal here
            raise PreprocessError(
                f"the PDF is structurally corrupt and could not be repaired: {repair_exc}"
            ) from repair_exc


def probe_page(page: pymupdf.Page, table: RoutingTable) -> tuple[TextLayer, int, int]:
    """
    Determine the page's text layer. Costs nothing and decides the whole route.

    Four outcomes, and the third is the trap:

    * **RICH** — many words with coherent boxes. Native extraction, zero OCR cost.
    * **SPARSE** — a few words, mostly a title block. Scanned; OCR required.
    * **NONE** — zero words. Scanned; OCR required.
    * **VECTOR_OUTLINED** — near-zero words but a very high vector path count.
      The page is not a raster, it is thousands of filled paths, and a naive probe
      calls it "scanned". It must be rasterised at high DPI before OCR or the
      small annotation text carrying door numbers and ratings is lost, producing
      an empty extraction with *high* OCR confidence (Risk R11).
    """
    words = page.get_text("words")
    drawings = page.get_drawings()
    word_count = len(words)
    path_count = len(drawings)

    if word_count < table.vector_outlined_max_words and path_count > table.vector_outlined_min_paths:
        layer = TextLayer.VECTOR_OUTLINED
    elif word_count >= table.rich_text_min_words:
        layer = TextLayer.RICH
    elif word_count == 0:
        layer = TextLayer.NONE
    else:
        layer = TextLayer.SPARSE

    return layer, word_count, path_count


def hash_page(page: pymupdf.Page) -> str:
    """
    SHA-256 of the page's normalised content stream, for addendum diffing (§4.7).

    Computed during the manifest pass regardless, which is what makes the
    "an addendum arrived" diff cheap to build.
    """
    try:
        raw = page.read_contents()
    except Exception:  # noqa: BLE001 - blank pages have no content stream
        raw = page.get_text("text").encode("utf-8")
    return hashlib.sha256(raw or b"").hexdigest()


# ---------------------------------------------------------------------------
# Step 2 — classification (§4.3)
# ---------------------------------------------------------------------------

def classify_by_bookmark(
    outline_titles: dict[int, str], page_number: int, table: RoutingTable
) -> tuple[PageClass, float] | None:
    """Tier 1 — PDF outline. Free, instant. Architectural sets are usually bookmarked by sheet."""
    title = outline_titles.get(page_number)
    if not title:
        return None
    hit = _match_anchor(title, table)
    return (hit, 0.95) if hit else None


def classify_by_title_block(
    page: pymupdf.Page, table: RoutingTable
) -> tuple[PageClass, float] | None:
    """
    Tier 2 — title block and sheet number. Free.

    Extracts the bottom-right ~15% of the page and keyword-matches it. Sheet
    numbers follow office conventions, so a prefix hit *raises* confidence rather
    than deciding outright.
    """
    rect = page.rect
    corner = pymupdf.Rect(
        rect.x0 + rect.width * (1 - TITLE_BLOCK_FRACTION * 2),
        rect.y0 + rect.height * (1 - TITLE_BLOCK_FRACTION),
        rect.x1,
        rect.y1,
    )
    text = page.get_text("text", clip=corner)
    if not text.strip():
        return None

    hit = _match_anchor(text, table)
    if hit:
        return hit, 0.85

    match = SHEET_NUMBER.search(text.upper())
    if match:
        prefix = f"{match.group(1)}{match.group(2)}"
        mapped = table.sheet_number_prefixes.get(prefix)
        if mapped:
            # Deliberately modest: sheet numbering varies between offices, and a
            # wrong confident answer here suppresses the cheaper tiers below.
            return PageClass(mapped), 0.60
    return None


def classify_by_keyword(text: str, table: RoutingTable) -> tuple[PageClass, float] | None:
    """
    Tier 3 — full-page anchors on the native text layer. Free, RICH pages only.

    Scored rather than first-match. A sheet index mentions "DOOR SCHEDULE" in a
    row of its contents table, and taking the first anchor hit would classify all
    65 pages of a real bid set that name a schedule anywhere as schedules — which
    is precisely the false-positive behaviour measured on the Dutch Bros set.
    """
    normalised = normalise_for_anchor(text)
    if not normalised:
        return None

    scores: dict[PageClass, float] = {}
    for class_name, anchors in table.anchors.items():
        try:
            page_class = PageClass(class_name)
        except ValueError:
            continue
        for anchor in anchors:
            needle = normalise_for_anchor(anchor)
            if not needle:
                continue
            occurrences = normalised.count(needle)
            if occurrences:
                # More distinct occurrences of a longer anchor is stronger
                # evidence the page IS the thing, not merely that it mentions it.
                weight = min(1.0, 0.5 + 0.1 * occurrences) * min(1.0, len(needle) / 12)
                scores[page_class] = max(scores.get(page_class, 0.0), weight)

    if not scores:
        return None

    # An index page names many schedules. If INDEX also matched, it wins: a
    # contents page listing four schedule types is an index, not four schedules.
    if PageClass.INDEX in scores and len(scores) > 2:
        return PageClass.INDEX, max(0.7, scores[PageClass.INDEX])

    best = max(scores.items(), key=lambda item: item[1])
    return best if best[1] >= table.min_keyword_confidence else None


def _match_anchor(text: str, table: RoutingTable) -> PageClass | None:
    normalised = normalise_for_anchor(text)
    for class_name, anchors in table.anchors.items():
        for anchor in anchors:
            if normalise_for_anchor(anchor) in normalised:
                try:
                    return PageClass(class_name)
                except ValueError:
                    continue
    return None


def classify_page(
    page: pymupdf.Page,
    page_number: int,
    text_layer: TextLayer,
    outline_titles: dict[int, str],
    table: RoutingTable,
) -> tuple[PageClass, float, ClassMethod | None]:
    """
    Run the tiers cheapest-first and stop at the first that resolves (§4.3).

    Tier 4 (Haiku on a thumbnail) is the only paid tier and is invoked separately
    by :func:`needs_model_classification` so that the whole batch of unresolved
    pages goes in one call rather than one call per page.

    Tier 5 is manual: unresolved pages default to UNKNOWN and are surfaced in the
    review UI as "pages the system did not read", where an estimator can force a
    read (Risk R12).
    """
    resolved = classify_by_bookmark(outline_titles, page_number, table)
    if resolved:
        return resolved[0], resolved[1], ClassMethod.BOOKMARK

    resolved = classify_by_title_block(page, table)
    if resolved:
        return resolved[0], resolved[1], ClassMethod.TITLE_BLOCK

    if text_layer == TextLayer.RICH:
        resolved = classify_by_keyword(page.get_text("text"), table)
        if resolved:
            return resolved[0], resolved[1], ClassMethod.KEYWORD

    # A page that matched no schedule anchor but carries thousands of vector paths
    # is a drawing sheet. Geometry is positive evidence and holds regardless of
    # text layer: architectural sheets carry dimension and callout text, so a rich
    # text layer does not make a plan sheet a document.
    #
    # DRAWING and UNKNOWN both route to SKIP, so this costs nothing either way —
    # but the distinction is load-bearing for Risk R12. UNKNOWN is surfaced to the
    # estimator as "pages the system did not read"; filling that list with 22
    # correctly-skipped plan sheets hides the pages that genuinely need a look.
    if len(page.get_drawings()) > table.drawing_min_vector_paths:
        return PageClass.DRAWING, 0.7, ClassMethod.TITLE_BLOCK

    if text_layer in (TextLayer.SPARSE, TextLayer.NONE, TextLayer.VECTOR_OUTLINED):
        return PageClass.DRAWING, 0.5, ClassMethod.KEYWORD

    return PageClass.UNKNOWN, 0.0, None


def needs_model_classification(probe: PageProbe, table: RoutingTable) -> bool:
    """Tier 4 candidates: only pages the free tiers left unresolved (§4.3)."""
    return (
        probe.page_class == PageClass.UNKNOWN.value
        or probe.class_confidence < table.min_keyword_confidence
    )


# ---------------------------------------------------------------------------
# Step 3 — the manifest pass
# ---------------------------------------------------------------------------

def _outline_titles(doc: pymupdf.Document) -> dict[int, str]:
    """Map 1-based page number to its outline title, when the PDF has bookmarks."""
    titles: dict[int, str] = {}
    try:
        for _level, title, page in doc.get_toc():
            if page and page > 0:
                titles.setdefault(page, title)
    except Exception:  # noqa: BLE001 - a malformed outline must not fail the job
        log.debug("document has no usable outline")
    return titles


def analyze_document(
    file_bytes: bytes, *, table: RoutingTable | None = None, max_cost_usd: Decimal | None = None
) -> list[PageProbe]:
    """
    Probe, classify, and route every page. **No Textract calls, no spend.**

    Raises :class:`BudgetExceeded` when the estimated cost of the whole document
    exceeds the configured guard, before a single OCR call is made.
    """
    table = table or load_routing_table()
    data, _repaired = validate_pdf(file_bytes)

    probes: list[PageProbe] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        outline = _outline_titles(doc)

        for index in range(len(doc)):
            page = doc[index]
            page_number = index + 1

            text_layer, word_count, path_count = probe_page(page, table)
            page_class, confidence, method = classify_page(
                page, page_number, text_layer, outline, table
            )
            decision = table.decide(page_class, text_layer)

            probes.append(
                PageProbe(
                    page_number=page_number,
                    width_pt=page.rect.width,
                    height_pt=page.rect.height,
                    # Honoured at raster time. A rotated sheet rendered without it
                    # produces polygons that overlay 90 degrees off (§4.5).
                    rotation=page.rotation,
                    native_word_count=word_count,
                    vector_path_count=path_count,
                    text_layer=text_layer.value,
                    page_class=page_class.value,
                    class_confidence=round(confidence, 4),
                    class_method=method.value if method else None,
                    ocr_route=decision.route.value,
                    route_reason=decision.reason,
                    ocr_cost_estimate=decision.cost_estimate,
                    page_hash=hash_page(page),
                    native_text=page.get_text("text")
                    if decision.route == OCRRoute.NATIVE_TEXT
                    else "",
                )
            )

    total = sum((p.ocr_cost_estimate for p in probes), Decimal("0"))
    if max_cost_usd is not None and total > max_cost_usd:
        raise BudgetExceeded(
            f"estimated OCR cost ${total:.2f} for {len(probes)} pages exceeds the "
            f"${max_cost_usd:.2f} per-document guard. Nothing has been spent. Raise "
            f"MAX_OCR_COST_PER_DOCUMENT_USD deliberately, or check whether the right "
            f"document was uploaded."
        )

    log.info(
        "document triaged",
        extra={
            "pages": len(probes),
            "estimated_cost_usd": str(total),
            "routes": {
                route: sum(1 for p in probes if p.ocr_route == route)
                for route in {p.ocr_route for p in probes}
            },
        },
    )
    return probes


def plan_splits(probes: list[PageProbe], max_pages: int) -> list[PageProbe]:
    """
    Assign split parts and page offsets (§4.6, resolving C16).

    Textract async accepts 500 MB / 3,000 pages per document; combined plan sets
    exceed it. Splitting is straightforward — **preserving provenance across the
    split is not.**

    Every part-local page number is converted back to the document-global number
    before ``doc_elements`` is written, because a citation must always point at a
    page number that means something in the PDF the estimator is looking at. The
    offset recorded here is what makes that conversion possible, and it is what
    keeps splitting idempotent: ``element_path`` is built from the global index,
    so re-running after a *different* split still produces identical identities.
    """
    for probe in probes:
        probe.split_part = (probe.page_number - 1) // max_pages
        probe.page_offset = probe.split_part * max_pages
    return probes


def summarise(probes: list[PageProbe]) -> dict:
    """Counts for logging and for the cost report."""
    by_route: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for probe in probes:
        by_route[probe.ocr_route] = by_route.get(probe.ocr_route, 0) + 1
        by_class[probe.page_class] = by_class.get(probe.page_class, 0) + 1
    return {
        "pages": len(probes),
        "by_route": by_route,
        "by_class": by_class,
        "estimated_cost_usd": str(sum((p.ocr_cost_estimate for p in probes), Decimal("0"))),
        "pages_ocr": sum(
            1
            for p in probes
            if p.ocr_route in (OCRRoute.TEXTRACT_TABLES.value, OCRRoute.TEXTRACT_TEXT.value)
        ),
        "pages_skipped": sum(1 for p in probes if p.ocr_route == OCRRoute.SKIP.value),
    }
