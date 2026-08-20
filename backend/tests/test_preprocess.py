"""
Preprocessing and triage (§4).

Triage is the single highest-value change in the specification — a ~23x reduction
on the dominant cost line and what makes NFR-6 reachable — and it introduces
exactly one new failure mode: a schedule the classifier did not recognise. These
tests exist to hold both ends of that trade.
"""

import io
from decimal import Decimal

import pymupdf
import pytest

from pipeline.routing import load_routing_table, normalise_for_anchor
from pipeline.stages.preprocess import (
    BudgetExceeded,
    EncryptedDocument,
    analyze_document,
    plan_splits,
    summarise,
)
from shared.enums import ClassMethod, OCRRoute, PageClass, TextLayer

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _pdf(build) -> bytes:
    doc = pymupdf.open()
    build(doc)
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def blank_pdf(pages: int = 1) -> bytes:
    return _pdf(lambda d: [d.new_page(width=612, height=792) for _ in range(pages)])


def text_pdf(text: str, *, filler_words: int = 80) -> bytes:
    """A page with a real text layer — enough words to probe as RICH."""

    def build(doc):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(
            pymupdf.Rect(72, 72, 540, 750),
            text + "\n" + " ".join(["lorem"] * filler_words),
            fontsize=10,
        )

    return _pdf(build)


def drawing_pdf(paths: int = 800) -> bytes:
    """
    A sheet with heavy vector geometry and a little dimension text.

    Each line is drawn separately: one committed ``Shape`` is a single path
    regardless of how many segments it holds, so batching them would produce
    ``vector_path_count == 1`` and quietly stop testing the thing under test.
    """

    def build(doc):
        page = doc.new_page(width=612, height=792)
        for i in range(paths):
            page.draw_line(
                pymupdf.Point(i % 500, 10), pymupdf.Point(i % 500, 700), width=0.3
            )
        page.insert_text(pymupdf.Point(72, 60), "3'-0\" TYP")

    return _pdf(build)


@pytest.fixture(scope="module")
def table():
    return load_routing_table()


# ---------------------------------------------------------------------------
# Probing (§4.2)
# ---------------------------------------------------------------------------

class TestTextLayerProbe:
    def test_blank_page_probes_as_none(self, table):
        probe = analyze_document(blank_pdf(), table=table)[0]
        assert probe.text_layer == TextLayer.NONE.value
        assert probe.native_word_count == 0

    def test_text_page_probes_as_rich(self, table):
        probe = analyze_document(text_pdf("GENERAL NOTES"), table=table)[0]
        assert probe.text_layer == TextLayer.RICH.value

    def test_vector_outlined_page_is_detected_not_called_scanned(self, table):
        """
        Risk R11 — the trap.

        Near-zero words plus a very high vector-path count is a page whose text was
        exported as outlines. A naive probe calls it "scanned"; OCR of a
        downsampled render then loses the small annotation text where door numbers
        and ratings live, producing an empty extraction with HIGH OCR confidence.
        """
        probe = analyze_document(drawing_pdf(paths=900), table=table)[0]
        assert probe.vector_path_count > table.vector_outlined_min_paths
        assert probe.native_word_count < table.vector_outlined_max_words
        assert probe.text_layer == TextLayer.VECTOR_OUTLINED.value


# ---------------------------------------------------------------------------
# Classification (§4.3)
# ---------------------------------------------------------------------------

class TestClassification:
    def test_door_schedule_anchor_is_detected(self, table):
        probe = analyze_document(text_pdf("DOOR SCHEDULE"), table=table)[0]
        assert probe.page_class == PageClass.DOOR_SCHEDULE.value
        assert probe.class_method == ClassMethod.KEYWORD.value

    def test_hardware_schedule_anchor_is_detected(self, table):
        probe = analyze_document(text_pdf("HARDWARE SETS HW-01"), table=table)[0]
        assert probe.page_class == PageClass.HARDWARE_SCHEDULE.value

    def test_letter_spaced_cad_title_still_matches(self, table):
        """
        ``D O O R   S C H E D U L E`` is common in CAD title text.

        A naive ``"DOOR SCHEDULE" in page_text`` misses every one of them, which
        would silently skip the one page in the set that matters.
        """
        probe = analyze_document(text_pdf("D O O R   S C H E D U L E"), table=table)[0]
        assert probe.page_class == PageClass.DOOR_SCHEDULE.value

    def test_anchor_normalisation_is_whitespace_and_case_insensitive(self):
        assert normalise_for_anchor("D O O R  schedule") == normalise_for_anchor("DOOR SCHEDULE")
        assert normalise_for_anchor("Door-Schedule:") == "DOORSCHEDULE"

    def test_class_does_not_leak_between_pages(self, table):
        """
        Regression: ``locals().get('page_class', ...)`` let page N's class persist
        into page N+1 whenever the later page took a different branch. A schedule
        followed by a drawing silently made the drawing a schedule too.
        """

        def build(doc):
            page = doc.new_page(width=612, height=792)
            page.insert_textbox(
                pymupdf.Rect(72, 72, 540, 750),
                "DOOR SCHEDULE\n" + " ".join(["lorem"] * 80),
                fontsize=10,
            )
            doc.new_page(width=612, height=792)  # blank; must NOT inherit

        probes = analyze_document(_pdf(build), table=table)
        assert probes[0].page_class == PageClass.DOOR_SCHEDULE.value
        assert probes[1].page_class != PageClass.DOOR_SCHEDULE.value

    def test_drawing_geometry_classifies_as_drawing_not_unknown(self, table):
        """
        Both route to SKIP, but the distinction is load-bearing for Risk R12.

        UNKNOWN is surfaced as "pages the system did not read". Filling that list
        with correctly-skipped plan sheets hides the pages that genuinely need a
        human look.
        """
        probe = analyze_document(drawing_pdf(paths=900), table=table)[0]
        assert probe.page_class == PageClass.DRAWING.value


# ---------------------------------------------------------------------------
# Routing (§4.4) — bottleneck B1
# ---------------------------------------------------------------------------

class TestRouting:
    def test_schedule_routes_to_tables_even_with_a_rich_text_layer(self, table):
        """Word positions alone do not give cell/row/column structure (§4.2)."""
        probe = analyze_document(text_pdf("DOOR SCHEDULE"), table=table)[0]
        assert probe.text_layer == TextLayer.RICH.value
        assert probe.ocr_route == OCRRoute.TEXTRACT_TABLES.value

    def test_prose_with_a_rich_text_layer_costs_nothing(self, table):
        probe = analyze_document(text_pdf("PART 1 - GENERAL"), table=table)[0]
        assert probe.ocr_route == OCRRoute.NATIVE_TEXT.value
        assert probe.ocr_cost_estimate == Decimal("0")

    def test_drawings_are_skipped_with_a_visible_reason(self, table):
        """§4.3 design rule: never silently skip."""
        probe = analyze_document(drawing_pdf(), table=table)[0]
        assert probe.ocr_route == OCRRoute.SKIP.value
        assert probe.route_reason, "a SKIP with no reason is a silent omission"

    def test_routing_table_is_configuration_not_code(self, table):
        """
        Risk R1: if CBC says ratings live in margin notes, DRAWING must become
        TEXTRACT_TEXT by editing JSON, not Python.
        """
        assert table.source.endswith(".json")
        assert table.content_hash and len(table.content_hash) == 16


# ---------------------------------------------------------------------------
# Cost guard and splitting
# ---------------------------------------------------------------------------

class TestGuards:
    def test_budget_guard_refuses_before_spending(self, table):
        """
        §10.3: the only control that catches an accidental 3,000-page upload
        *before* the money is gone.
        """
        pdf = _pdf(
            lambda d: [
                d.new_page(width=612, height=792).insert_textbox(
                    pymupdf.Rect(72, 72, 540, 750),
                    "DOOR SCHEDULE\n" + " ".join(["lorem"] * 80),
                    fontsize=10,
                )
                for _ in range(6)
            ]
        )
        with pytest.raises(BudgetExceeded) as exc:
            analyze_document(pdf, table=table, max_cost_usd=Decimal("0.05"))
        assert "Nothing has been spent" in str(exc.value)

    def test_encrypted_pdf_reports_rather_than_crashing(self, table):
        doc = pymupdf.open()
        doc.new_page()
        buffer = io.BytesIO()
        doc.save(buffer, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
        doc.close()
        with pytest.raises(EncryptedDocument):
            analyze_document(buffer.getvalue(), table=table)

    def test_split_offsets_are_recorded_for_global_page_numbers(self, table):
        """
        §4.6: a citation must point at a page number that means something in the
        original PDF the estimator is looking at.
        """
        probes = plan_splits(analyze_document(blank_pdf(5), table=table), max_pages=2)
        assert [p.split_part for p in probes] == [0, 0, 1, 1, 2]
        assert [p.page_offset for p in probes] == [0, 0, 2, 2, 4]

    def test_page_hash_is_stable_and_hex(self, table):
        first = analyze_document(text_pdf("GENERAL NOTES"), table=table)[0]
        second = analyze_document(text_pdf("GENERAL NOTES"), table=table)[0]
        assert len(first.page_hash) == 64
        int(first.page_hash, 16)
        assert first.page_hash == second.page_hash, "identical content must hash identically (§4.7)"


class TestSummary:
    def test_summary_counts_routes_and_cost(self, table):
        probes = analyze_document(text_pdf("DOOR SCHEDULE"), table=table)
        report = summarise(probes)
        assert report["pages"] == 1
        assert report["pages_ocr"] == 1
        assert report["by_route"][OCRRoute.TEXTRACT_TABLES.value] == 1
