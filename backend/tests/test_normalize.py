"""
Normalisation — the idempotency contract (§7.2, §4.6).

The Phase 1 exit criterion the whole traceability chain rests on:

    Re-running normalisation reproduces identical element identities
    (``unique (document_id, element_path)`` holds, no orphaned citations).

Textract mints a fresh ``Block.Id`` on every job. Keying elements by it means a
re-run produces a completely different identity set, orphaning every citation an
estimator has already reviewed. Positional paths are what make the re-run safe.
"""

import pytest

from pipeline.stages.normalize import (
    ELEMENT_COLUMNS,
    parse_blocks,
    parse_native_text,
    scale_native_elements,
)
from shared.enums import ElementType

# ---------------------------------------------------------------------------
# Textract block fixtures
# ---------------------------------------------------------------------------

def word(block_id: str, text: str, page: int = 1, confidence: float = 99.2) -> dict:
    return {
        "BlockType": "WORD",
        "Id": block_id,
        "Page": page,
        "Text": text,
        "Confidence": confidence,
        "Geometry": {
            "Polygon": [
                {"X": 0.1, "Y": 0.2}, {"X": 0.3, "Y": 0.2},
                {"X": 0.3, "Y": 0.25}, {"X": 0.1, "Y": 0.25},
            ],
            "BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.2, "Height": 0.05},
        },
    }


def cell(block_id: str, text: str, row: int, col: int, page: int = 1, header: bool = False) -> dict:
    return {
        "BlockType": "CELL",
        "Id": block_id,
        "Page": page,
        "Text": text,
        "RowIndex": row,
        "ColumnIndex": col,
        "Confidence": 95.0,
        "EntityTypes": ["COLUMN_HEADER"] if header else [],
        "Geometry": {
            "Polygon": [{"X": 0, "Y": 0}] * 4,
            "BoundingBox": {"Left": 0, "Top": 0, "Width": 1, "Height": 1},
        },
    }


def table(block_id: str, child_ids: list[str], page: int = 1) -> dict:
    return {
        "BlockType": "TABLE",
        "Id": block_id,
        "Page": page,
        "Relationships": [{"Type": "CHILD", "Ids": child_ids}],
        "Geometry": {"Polygon": [], "BoundingBox": {}},
    }


def schedule(prefix: str) -> list[dict]:
    """The same logical table as Textract would return it on two different jobs."""
    return [
        word(f"{prefix}-w1", "DOOR"),
        table(f"{prefix}-t1", [f"{prefix}-c1", f"{prefix}-c2", f"{prefix}-c3"]),
        cell(f"{prefix}-c1", "NUMBER", 1, 1, header=True),
        cell(f"{prefix}-c2", "101", 2, 1),
        cell(f"{prefix}-c3", "3070", 2, 2),
    ]


# ---------------------------------------------------------------------------
# THE IDEMPOTENCY CONTRACT
# ---------------------------------------------------------------------------

class TestElementPathIsPositionalNotBlockId:
    def test_two_jobs_over_the_same_document_produce_identical_paths(self):
        """
        The Phase 1 exit criterion.

        Both runs describe the same page; only Textract's internal ids differ.
        """
        first = [e.element_path for e in parse_blocks(schedule("runA"))]
        second = [e.element_path for e in parse_blocks(schedule("runB"))]
        assert first == second
        assert first, "expected some elements"

    def test_element_path_does_not_contain_the_block_id(self):
        """
        Regression on the defect this replaces.

        The previous implementation wrote ``blocks/{Block.Id}``, which is
        regenerated per job — a re-run orphaned every citation.
        """
        for element in parse_blocks(schedule("runA")):
            assert "runA" not in element.element_path
            assert element.element_path.startswith("pages/")

    def test_table_id_is_stable_across_jobs(self):
        """Cells must still group into the same table after a re-run."""
        first = sorted({str(e.table_id) for e in parse_blocks(schedule("runA")) if e.table_id})
        second = sorted({str(e.table_id) for e in parse_blocks(schedule("runB")) if e.table_id})
        assert first == second and first

    def test_paths_are_unique_within_a_document(self):
        """``unique (document_id, element_path)`` must be satisfiable."""
        paths = [e.element_path for e in parse_blocks(schedule("runA"))]
        assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# Global page numbers (§4.6)
# ---------------------------------------------------------------------------

class TestGlobalPageNumbers:
    def test_split_offset_is_applied_before_the_row_is_written(self):
        element = parse_blocks([word("id", "text", page=1)], page_offset=1000)[0]
        assert element.page_number == 1001

    def test_offset_appears_in_the_path_so_splits_stay_distinct(self):
        element = parse_blocks([word("id", "text", page=1)], page_offset=1000)[0]
        assert element.element_path == "pages/1001/words/0"

    def test_same_global_page_yields_the_same_path_under_different_splits(self):
        """
        Splitting must be idempotent: page 1001 reached as part 1 page 1 or as
        part 0 page 1001 is the same page and must produce the same identity.
        """
        via_split = parse_blocks([word("a", "x", page=1)], page_offset=1000)[0]
        via_whole = parse_blocks([word("b", "x", page=1001)], page_offset=0)[0]
        assert via_split.element_path == via_whole.element_path


# ---------------------------------------------------------------------------
# Field fidelity
# ---------------------------------------------------------------------------

class TestFieldMapping:
    def test_confidence_is_scaled_to_0_1(self):
        element = parse_blocks([word("id", "x", confidence=99.2)])[0]
        assert element.ocr_confidence == pytest.approx(0.992)

    def test_missing_confidence_stays_none_rather_than_becoming_one(self):
        """
        A block with no Confidence was not recognised — it came from the native
        text layer. Storing 1.0 would let composite confidence claim certainty no
        measurement supports (§5.9).
        """
        block = word("id", "x")
        del block["Confidence"]
        assert parse_blocks([block])[0].ocr_confidence is None

    def test_row_and_column_are_normalised_to_zero_indexed(self):
        """Textract is 1-indexed; §7.2 stores 0-indexed."""
        element = [e for e in parse_blocks(schedule("runA")) if e.element_type == "table_cell"][0]
        assert element.row_index == 0 and element.col_index == 0

    def test_column_header_flag_is_carried(self):
        cells = [e for e in parse_blocks(schedule("runA")) if e.element_type == "table_cell"]
        assert cells[0].column_header is True
        assert cells[1].column_header is False

    def test_polygon_is_eight_floats_and_bbox_is_derived(self):
        element = parse_blocks([word("id", "x")])[0]
        assert len(element.polygon) == 8
        assert element.bbox == pytest.approx((0.1, 0.2, 0.3, 0.25))

    def test_structural_blocks_are_not_stored_as_elements(self):
        """PAGE and TABLE are containers; their geometry is implied by their children."""
        types = {e.element_type for e in parse_blocks(schedule("runA"))}
        assert types <= {ElementType.WORD.value, ElementType.TABLE_CELL.value}

    def test_copy_column_list_matches_the_row_tuple(self):
        """A drifted column list corrupts every row silently."""
        element = parse_blocks([word("id", "x")])[0]
        # id, document_id, path, page, type, text + 8 polygon + 4 bbox + conf +
        # reading_order + table_id + row + col + header + created + updated
        assert len(ELEMENT_COLUMNS) == 26
        assert len(element.polygon) + len(element.bbox) == 12

    def test_empty_input_yields_nothing(self):
        assert parse_blocks([]) == []


# ---------------------------------------------------------------------------
# Native text path (the zero-cost route)
# ---------------------------------------------------------------------------

class TestNativeText:
    def test_native_words_carry_no_ocr_confidence(self):
        elements = parse_native_text(3, [(10.0, 20.0, 30.0, 40.0, "3070", 0, 0, 0)])
        assert elements[0].ocr_confidence is None
        assert elements[0].page_number == 3

    def test_native_paths_share_the_positional_scheme(self):
        elements = parse_native_text(3, [(10.0, 20.0, 30.0, 40.0, "3070", 0, 0, 0)])
        assert elements[0].element_path == "pages/3/words/0"

    def test_points_are_scaled_to_page_fractions(self):
        """
        The viewer overlays polygons as CSS percentages, so geometry must be 0-1
        regardless of which route produced it (bottleneck B5).
        """
        elements = scale_native_elements(
            parse_native_text(1, [(61.2, 79.2, 183.6, 198.0, "x", 0, 0, 0)]),
            width_pt=612.0,
            height_pt=792.0,
        )
        assert elements[0].bbox == pytest.approx((0.1, 0.1, 0.3, 0.25))
        assert all(0.0 <= v <= 1.0 for v in elements[0].polygon)
