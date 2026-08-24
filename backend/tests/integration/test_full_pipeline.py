"""
EXTRACT → LINK → §5.11 → MATCH → PRICE, in one run (§3.3 steps 8-11).

The individual stages are covered elsewhere. What this file asserts is that they
are actually *connected* — which is not a given: before this test existed,
``resolve_hardware_sets`` was fully written, schema'd, and prompted, and called by
nothing, and ``PipelineStage.PRICE`` was an enum value no code ever wrote a row
for. Both looked finished from every angle except running them.

Bedrock is stubbed. The point is not to test Claude; it is to test that a
well-formed model answer travels all the way to a priced draft quote without a
human in the middle, because that journey is what NFR-6 promises.
"""

import uuid
from decimal import Decimal

import pytest
from factories import (
    CatalogItemFactory,
    DocElementFactory,
    DocumentFactory,
    MarginBandFactory,
    ProjectFactory,
)
from openings.models import HardwareSetComponent, Match, Opening
from projects.models import PipelineJob
from quotes.models import Quote

from pipeline.stages import extract as extract_stage
from pipeline.stages import run_extraction
from shared.enums import PipelineJobStatus, PipelineStage, QuoteStatus

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


DOOR_HEADER = ["DOOR NO.", "SIZE", "HAND", "FINISH", "LABEL", "HW SET"]
DOOR_ROWS = [
    ["101", "3070", "LH", "US26D", "90 MIN", "HW-3"],
    ["102", "3070", "LH", "US26D", "90 MIN", "HW-3"],
]
HARDWARE_HEADER = ["QTY", "ITEM", "MFR", "PART", "FINISH"]
HARDWARE_ROWS = [
    ["3", "Full mortise butt hinge", "Hager", "BB1279", "US26D"],
    ["1", "Surface closer", "LCN", "4040XP", "US26D"],
]


def _table(document, page, header, rows):
    """Write one table exactly as normalisation does, and index its cells."""
    table_id = uuid.uuid4()
    cells = {}
    index = 0
    for column, text in enumerate(header):
        cells[(0, column)] = DocElementFactory(
            document=document,
            element_path=f"pages/{page}/tables/0/cells/{index}",
            element_type="table_cell",
            text=text,
            table_id=table_id,
            row_index=0,
            col_index=column,
            column_header=True,
            page_number=page,
        )
        index += 1
    for row_number, row in enumerate(rows, start=1):
        for column, text in enumerate(row):
            cells[(row_number, column)] = DocElementFactory(
                document=document,
                element_path=f"pages/{page}/tables/0/cells/{index}",
                element_type="table_cell",
                text=text,
                table_id=table_id,
                row_index=row_number,
                col_index=column,
                column_header=False,
                page_number=page,
            )
            index += 1
    return str(table_id), cells


@pytest.fixture
def bid_set():
    """
    A door schedule on one sheet and its Division 08 hardware definition on
    another — the cross-schedule shape §5.11 exists for.
    """
    MarginBandFactory(product_type_band="COMMODITY", target_margin_pct=Decimal("0.2700"))
    project = ProjectFactory()
    document = DocumentFactory(project=project)
    door_id, door_cells = _table(document, 15, DOOR_HEADER, DOOR_ROWS)
    hw_id, hw_cells = _table(document, 29, HARDWARE_HEADER, HARDWARE_ROWS)

    CatalogItemFactory(
        vendor="Hager", sku="BB1279-626", description="Full mortise butt hinge",
        fire_rating_minutes=90, list_price=Decimal("38.00"),
    )
    CatalogItemFactory(
        vendor="LCN", sku="4040XP-626", description="Surface closer",
        fire_rating_minutes=90, list_price=Decimal("220.00"),
    )
    return {
        "project": project,
        "document": document,
        "door_table": door_id,
        "door_cells": door_cells,
        "hardware_table": hw_id,
        "hardware_cells": hw_cells,
    }


@pytest.fixture
def stub_model(bid_set, monkeypatch):
    """
    A well-behaved model: cites only what it was shown, never normalises.

    Routed by tool name rather than by call order, so the test does not silently
    depend on the sequence the pipeline happens to make its calls in.
    """
    door_cells = bid_set["door_cells"]
    hw_cells = bid_set["hardware_cells"]

    def cited(cells, row, column, value):
        return {
            "value": value,
            "source_element_ids": [str(cells[(row, column)].id)],
            "confidence_llm": 0.97,
        }

    def opening_record(row, door):
        return {
            "opening_id": door,
            "needs_review": False,
            "fields": {
                "door_number": cited(door_cells, row, 0, door),
                "size": cited(door_cells, row, 1, "3070"),
                "handing": cited(door_cells, row, 2, "LH"),
                "finish": cited(door_cells, row, 3, "US26D"),
                "fire_rating": cited(door_cells, row, 4, "90 MIN"),
                "hardware_group": cited(door_cells, row, 5, "HW-3"),
                "alternate_designation": {
                    "value": None, "source_element_ids": [], "confidence_llm": None
                },
            },
        }

    def component(row):
        return {
            "quantity": cited(hw_cells, row, 0, HARDWARE_ROWS[row - 1][0]),
            "description": cited(hw_cells, row, 1, HARDWARE_ROWS[row - 1][1]),
            "manufacturer": cited(hw_cells, row, 2, HARDWARE_ROWS[row - 1][2]),
            "part_number": cited(hw_cells, row, 3, HARDWARE_ROWS[row - 1][3]),
            "finish": cited(hw_cells, row, 4, HARDWARE_ROWS[row - 1][4]),
        }

    class Response:
        input_tokens = 100
        output_tokens = 50
        cache_read_tokens = 0

        def __init__(self, payload):
            self.payload = payload

    def fake_invoke(*, model_id, system, messages, tool_spec, tool_name, **kwargs):
        if tool_name == "classify_tables":
            return Response(
                {
                    "tables": [
                        {"table_id": bid_set["door_table"], "classification": "DOOR_SCHEDULE"},
                        {
                            "table_id": bid_set["hardware_table"],
                            "classification": "HARDWARE_SCHEDULE",
                        },
                    ]
                }
            )
        if tool_name == "resolve_hardware_sets":
            return Response(
                {
                    "sets": [
                        {
                            "hardware_group": "HW-3",
                            "resolved": True,
                            "explicit_part": False,
                            "components": [component(1), component(2)],
                            "confidence": 0.94,
                        }
                    ]
                }
            )
        return Response({"openings": [opening_record(1, "101"), opening_record(2, "102")]})

    from pipeline.llm import bedrock

    monkeypatch.setattr(bedrock, "invoke", fake_invoke)
    monkeypatch.setattr(extract_stage.bedrock, "invoke", fake_invoke)
    monkeypatch.setattr(
        extract_stage, "resolve_models", lambda: ("model-premium", "model-cheap")
    )

    from shared import config as shared_config

    settings_obj = shared_config.get_settings()
    monkeypatch.setattr(
        type(settings_obj),
        "require_bedrock",
        lambda self: ("model-premium", "model-cheap"),
    )
    return fake_invoke


class TestOneRunReachesAPricedDraft:
    def test_the_whole_chain_connects(self, bid_set, stub_model):
        run_extraction.run(bid_set["document"])
        document, project = bid_set["document"], bid_set["project"]

        # EXTRACT + LINK
        assert Opening.objects.filter(project=project).count() == 2

        # §5.11 — the callout resolved rather than dead-ending at "HW-3"
        components = HardwareSetComponent.objects.filter(project=project)
        assert components.count() == 2
        assert set(components.values_list("part_number", flat=True)) == {"BB1279", "4040XP"}
        assert components.filter(resolved=False).count() == 0

        # MATCH — the door and each component, per opening
        assert Match.objects.filter(hardware_component__isnull=True).count() >= 2
        assert Match.objects.filter(hardware_component__isnull=False).exists()

        # PRICE — a draft quote exists and has been totalled
        quote = Quote.objects.get(project=project, status=QuoteStatus.DRAFT.value)
        assert quote.lines.filter(hardware_component__isnull=False).count() == 4, (
            "two openings x two components; hardware is most of a real CBC quote"
        )
        assert quote.grand_total > 0

        # Every stage wrote its own row, PRICE included.
        stages = dict(
            PipelineJob.objects.filter(document=document).values_list("stage", "status")
        )
        for stage in (PipelineStage.EXTRACT, PipelineStage.LINK, PipelineStage.MATCH,
                      PipelineStage.PRICE):
            assert stages.get(stage.value) == PipelineJobStatus.COMPLETED.value, stage

    def test_a_hinge_quantity_survives_to_the_quote_line(self, bid_set, stub_model):
        """Three hinges per opening, one closer. A defaulted 1 would under-quote."""
        run_extraction.run(bid_set["document"])
        quote = Quote.objects.get(project=bid_set["project"])
        hinges = quote.lines.filter(hardware_component__part_number="BB1279")
        assert all(line.quantity == 3 for line in hinges)
        assert all(
            line.quantity == 1
            for line in quote.lines.filter(hardware_component__part_number="4040XP")
        )

    def test_a_second_run_does_not_rebuild_over_the_draft(self, bid_set, stub_model):
        """
        The second document on a bid — an addendum, a separate hardware spec —
        must not silently rewrite lines an estimator has been working on.
        """
        run_extraction.run(bid_set["document"])
        quote = Quote.objects.get(project=bid_set["project"])
        quote.lines.filter(hardware_component__isnull=True, opening__isnull=False).update(
            our_cost=Decimal("999.0000")
        )

        run_extraction.run(bid_set["document"])

        assert Quote.objects.filter(project=bid_set["project"]).count() == 1
        assert quote.lines.filter(our_cost=Decimal("999.0000")).exists()


class TestAnUnresolvedCalloutIsRecordedNotInvented:
    def test_no_definition_table_means_every_callout_is_flagged(
        self, bid_set, stub_model, monkeypatch
    ):
        """
        The hardware sheet is missing from this bid set. The system must say so —
        not produce what an HW-3 usually contains (§5.11, NFR-2).
        """
        from pipeline.llm import bedrock

        original = bedrock.invoke

        def no_hardware_table(*, tool_name, **kwargs):
            if tool_name == "classify_tables":
                return type(
                    "R",
                    (),
                    {
                        "payload": {
                            "tables": [
                                {
                                    "table_id": bid_set["door_table"],
                                    "classification": "DOOR_SCHEDULE",
                                },
                                {
                                    "table_id": bid_set["hardware_table"],
                                    "classification": "GENERAL_NOTES",
                                },
                            ]
                        },
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_read_tokens": 0,
                    },
                )()
            return original(tool_name=tool_name, **kwargs)

        monkeypatch.setattr(extract_stage.bedrock, "invoke", no_hardware_table)
        run_extraction.run(bid_set["document"])

        rows = HardwareSetComponent.objects.filter(project=bid_set["project"])
        assert rows.count() == 1
        unresolved = rows.get()
        assert unresolved.resolved is False
        assert unresolved.hardware_group == "HW-3"
        assert unresolved.description == "", "nothing may be filled in from general knowledge"
        assert not Match.objects.filter(hardware_component=unresolved).exists()
