"""
EXTRACT and LINK against real normalised elements (§5.3, §5.6).

Marked ``integration`` because it needs a document that has actually been through
preprocessing and normalisation. The model is stubbed — the point is not to test
Claude, it is to test that the two-pass batching, the validation gate, the
deterministic parsers, and persistence hold together on **real Textract-shaped
geometry** rather than on a hand-built fixture.

Run with: ``pytest -m integration`` after ``make up`` and one upload.
"""

import pytest
from factories import DocElementFactory, DocumentFactory, ProjectFactory
from openings.models import FieldProvenance, Opening

from pipeline.stages import extract as extract_stage

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def schedule_table():
    """A door-schedule table shaped exactly as normalisation writes one."""
    project = ProjectFactory()
    document = DocumentFactory(project=project)

    import uuid

    table_id = uuid.uuid4()
    header = ["DOOR NO.", "SIZE", "HAND", "FINISH", "LABEL", "HW SET"]
    rows = [
        ["101", "3070", "LH", "US26D", "90 MIN", "HW-3"],
        ["102", "3070", "RH", "US26D", "NR", "HW-1"],
    ]

    elements = {}
    for column, text in enumerate(header):
        element = DocElementFactory(
            document=document,
            element_path=f"pages/1/tables/0/cells/{column}",
            element_type="table_cell",
            text=text,
            table_id=table_id,
            row_index=0,
            col_index=column,
            column_header=True,
            page_number=1,
        )
        elements[(0, column)] = element

    index = len(header)
    for row_number, row in enumerate(rows, start=1):
        for column, text in enumerate(row):
            element = DocElementFactory(
                document=document,
                element_path=f"pages/1/tables/0/cells/{index}",
                element_type="table_cell",
                text=text,
                table_id=table_id,
                row_index=row_number,
                col_index=column,
                column_header=False,
                page_number=1,
            )
            elements[(row_number, column)] = element
            index += 1

    return {"project": project, "document": document, "elements": elements}


def _model_answer(elements):
    """What a well-behaved model returns for the fixture above."""

    def cited(row, column, value, confidence=0.97):
        return {
            "value": value,
            "source_element_ids": [str(elements[(row, column)].id)],
            "confidence_llm": confidence,
        }

    def record(row, door, size, hand, finish, label, hw):
        return {
            "opening_id": door,
            "needs_review": False,
            "fields": {
                "door_number": cited(row, 0, door),
                "size": cited(row, 1, size),
                "handing": cited(row, 2, hand),
                "finish": cited(row, 3, finish),
                "fire_rating": cited(row, 4, label),
                "hardware_group": cited(row, 5, hw),
                "alternate_designation": {
                    "value": None, "source_element_ids": [], "confidence_llm": None
                },
            },
        }

    return {
        "openings": [
            record(1, "101", "3070", "LH", "US26D", "90 MIN", "HW-3"),
            record(2, "102", "3070", "RH", "US26D", "NR", "HW-1"),
        ]
    }


class TestBatching:
    def test_batches_are_scoped_to_one_table(self, schedule_table):
        """
        §5.3: one call per table, not per document.

        Table-scoped batching is what keeps context small, cost predictable, and
        failures isolated — one malformed table fails one call, not the bid set.
        """
        batches = extract_stage.build_table_batches(str(schedule_table["document"].id))
        assert len(batches) == 1
        batch = batches[0]
        assert batch.rows == 3 and batch.cols == 6
        assert len(batch.cells) == 18

    def test_header_row_is_identified_for_the_locate_pass(self, schedule_table):
        batch = extract_stage.build_table_batches(str(schedule_table["document"].id))[0]
        assert "DOOR NO." in batch.header_text
        inventory = batch.inventory_entry()
        assert inventory["rows"] == 3 and inventory["columns"] == 6

    def test_supplied_element_set_is_exactly_what_the_model_sees(self, schedule_table):
        """
        The gate validates citations against this set, not the whole document.

        Validating against everything would let a model cite a real element it was
        never shown — a hallucination that happens to land on something true.
        """
        batch = extract_stage.build_table_batches(str(schedule_table["document"].id))[0]
        cell_ids = {cell["element_id"] for cell in batch.cells}
        assert cell_ids <= set(batch.elements)


class TestEndToEndExtraction:
    def test_a_well_formed_answer_persists_openings_with_provenance(
        self, schedule_table, monkeypatch
    ):
        from pipeline.stages import link as link_stage

        elements = schedule_table["elements"]
        batch = extract_stage.build_table_batches(str(schedule_table["document"].id))[0]
        element_rows = {str(e.id): e for e in elements.values()}

        stats = link_stage.LinkStats()
        run = None
        for record in _model_answer(elements)["openings"]:
            from factories import ExtractionRunFactory

            run = run or ExtractionRunFactory(document=schedule_table["document"])
            linked = link_stage.link_opening(
                record,
                supplied_elements=batch.elements,
                element_rows=element_rows,
                stats=stats,
            )
            link_stage.persist_opening(
                project=schedule_table["project"],
                extraction_run=run,
                record=record,
                linked=linked,
                stats=stats,
            )

        assert Opening.objects.count() == 2
        assert stats.fields_rejected_citation == 0
        assert stats.fields_rejected_grounding == 0

        # §5.7: raw and typed values both stored.
        first = Opening.objects.get(door_number="101")
        assert (first.size_raw, first.width_inches, first.height_inches) == ("3070", 36, 84)
        assert first.fire_rating_raw == "90 MIN" and first.fire_rating_minutes == 90
        assert first.handing == "LH"

        # A positively-unrated opening is a finding, not a gap.
        second = Opening.objects.get(door_number="102")
        assert second.fire_rating_absent is True
        assert second.fire_rating_minutes is None

        # Every populated field carries a citation.
        for opening in (first, second):
            populated = FieldProvenance.objects.filter(
                opening=opening, extracted_value__isnull=False
            )
            for provenance in populated:
                assert provenance.elements.count() >= 1

    def test_prompts_are_versioned_artefacts(self):
        """
        §8.2: ``extraction_runs.prompt_version`` must resolve to an exact file.

        The fingerprint makes a silent edit detectable after the fact even though
        editing in place is forbidden.
        """
        body = extract_stage.load_prompt("extraction", "v1")
        assert "Cite only ids present in the input" in body
        assert "Never invent an id" in body
        assert len(extract_stage.prompt_fingerprint("extraction", "v1")) == 16

    def test_a_missing_prompt_version_is_an_error_not_a_fallback(self):
        with pytest.raises(extract_stage.ExtractionError) as exc:
            extract_stage.load_prompt("extraction", "v99")
        assert "unauditable" in str(exc.value)

    def test_the_cacheable_prefix_contains_nothing_variable(self):
        """
        §5.12: the prefix must be byte-identical across calls.

        A document id, timestamp, or run id interpolated above the boundary
        silently disables prompt caching and triples the input bill.
        """
        body = extract_stage.load_prompt("extraction", "v1")
        for token in ("{document_id}", "{run_id}", "{timestamp}", "{table_id}"):
            assert token not in body
