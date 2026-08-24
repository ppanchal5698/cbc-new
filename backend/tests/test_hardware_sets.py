"""
Cross-schedule resolution (§5.11) and what it must refuse to invent.

A door schedule says ``HW-3``. The Division 08 spec section defines what ``HW-3``
contains. Joining them is most of a real CBC quote — hardware, not the door slab,
is where the lines are (§1.3, §1.4).

It is also the single easiest place in the system to hallucinate something
plausible. Every language model knows roughly what a commercial hardware set
contains: hinges, a closer, a lock, a kick plate. Producing that list when the
document does not contain it would look like a working feature and would put
invented parts on a priced quote. So the load-bearing test here is not that
resolution works — it is that an unresolved callout stays unresolved and says so.
"""

import pytest
from factories import (
    CatalogItemFactory,
    DocElementFactory,
    ExtractionRunFactory,
    HardwareSetComponentFactory,
    OpeningFactory,
    ProjectFactory,
)
from openings.models import FieldProvenance, HardwareSetComponent, Match

from pipeline.stages import link as link_stage
from pipeline.stages.match import CatalogSnapshot, MatchCriteria, match_criteria, match_project
from shared.enums import ReviewState

pytestmark = pytest.mark.django_db


def cited(value, element_id, confidence=0.95):
    return {
        "value": value,
        "source_element_ids": [element_id],
        "confidence_llm": confidence,
    }


@pytest.fixture
def spec_elements():
    """Elements from a hardware-set definition block, as normalisation writes them."""
    texts = {
        "qty": "3",
        "desc": "Full mortise butt hinge",
        "mfr": "Hager",
        "part": "BB1279",
        "finish": "US26D",
    }
    rows = {
        key: DocElementFactory(
            text=text,
            element_path=f"pages/9/tables/1/cells/{i}",
            # The Division 08 spec section is a different sheet from the door
            # schedule; the whole point of resolution is joining across them.
            page_number=9,
        )
        for i, (key, text) in enumerate(texts.items())
    }
    supplied = {str(row.id): row.text for row in rows.values()}
    element_rows = {str(row.id): row for row in rows.values()}
    return rows, supplied, element_rows


# ---------------------------------------------------------------------------
# The negative case that matters
# ---------------------------------------------------------------------------

class TestAnUnresolvedSetIsNeverInvented:
    def test_unresolved_callout_persists_as_a_flagged_row_with_no_components(self):
        project = ProjectFactory()
        run = ExtractionRunFactory()
        stats = link_stage.LinkStats()

        link_stage.persist_hardware_set(
            project=project,
            extraction_run=run,
            hardware_group="HW-7",
            resolved=False,
            explicit_part=False,
            components=[],
            stats=stats,
        )

        row = HardwareSetComponent.objects.get(hardware_group="HW-7")
        assert row.resolved is False
        assert row.description == "", "an unresolved set must describe nothing"
        assert row.review_state == ReviewState.FLAGGED.value
        assert "NOT been" in row.review_notes, "the refusal must be legible to an estimator"
        assert stats.hardware_sets_unresolved == 1
        assert stats.hardware_components_written == 0

    def test_an_unresolved_set_produces_no_catalogue_matches(self):
        """
        Matching an unresolved callout would propose real parts for a set nobody
        has established exists — a hallucination laundered through a deterministic
        engine.
        """
        project = ProjectFactory()
        opening = OpeningFactory(project=project, hardware_group="HW-7")
        HardwareSetComponentFactory(
            project=project,
            extraction_run=opening.extraction_run,
            hardware_group="HW-7",
            resolved=False,
            description="",
            manufacturer=None,
            part_number=None,
        )
        CatalogItemFactory(fire_rating_minutes=90)

        counts = match_project(project, snapshot=CatalogSnapshot.load())
        assert counts["components"] == 0
        assert not Match.objects.filter(hardware_component__isnull=False).exists()

    def test_a_component_claiming_an_element_it_was_not_shown_is_rejected(self, spec_elements):
        """
        The §5.6 gate is not re-implemented for components — it is the same gate.
        This is the fabricated-citation test, aimed at the hardware path.
        """
        _, supplied, element_rows = spec_elements
        stats = link_stage.LinkStats()

        linked = link_stage.link_component(
            {"description": cited("Full mortise butt hinge", "el-does-not-exist")},
            supplied_elements=supplied,
            element_rows=element_rows,
            stats=stats,
        )

        description = next(item for item in linked if item.name == "description")
        assert not description.accepted
        assert stats.fields_rejected_citation == 1

    def test_a_component_value_not_present_in_its_cited_element_is_rejected(self, spec_elements):
        """Value grounding, on the hardware path. A real citation is not enough."""
        rows, supplied, element_rows = spec_elements
        stats = link_stage.LinkStats()

        linked = link_stage.link_component(
            # Cites the *manufacturer* cell, which says "Hager", for a closer.
            {"description": cited("Surface closer", str(rows["mfr"].id))},
            supplied_elements=supplied,
            element_rows=element_rows,
            stats=stats,
        )

        description = next(item for item in linked if item.name == "description")
        assert not description.accepted
        assert stats.fields_rejected_grounding == 1

    def test_a_component_with_no_accepted_description_is_not_written(self, spec_elements):
        """Everything about a component keys off what it is."""
        _, supplied, element_rows = spec_elements
        stats = link_stage.LinkStats()
        run = ExtractionRunFactory()

        link_stage.persist_hardware_set(
            project=ProjectFactory(),
            extraction_run=run,
            hardware_group="HW-3",
            resolved=True,
            explicit_part=False,
            components=[
                link_stage.link_component(
                    {"description": cited("Surface closer", "el-fabricated")},
                    supplied_elements=supplied,
                    element_rows=element_rows,
                    stats=stats,
                )
            ],
            stats=stats,
        )
        assert not HardwareSetComponent.objects.filter(extraction_run=run).exists()


# ---------------------------------------------------------------------------
# The positive path
# ---------------------------------------------------------------------------

class TestResolvedComponentsCarryTheirProvenance:
    def test_a_resolved_component_is_written_with_citations(self, spec_elements):
        rows, supplied, element_rows = spec_elements
        project, run = ProjectFactory(), ExtractionRunFactory()
        stats = link_stage.LinkStats()

        linked = link_stage.link_component(
            {
                "quantity": cited("3", str(rows["qty"].id)),
                "description": cited("Full mortise butt hinge", str(rows["desc"].id)),
                "manufacturer": cited("Hager", str(rows["mfr"].id)),
                "part_number": cited("BB1279", str(rows["part"].id)),
                "finish": cited("US26D", str(rows["finish"].id)),
            },
            supplied_elements=supplied,
            element_rows=element_rows,
            stats=stats,
        )
        link_stage.persist_hardware_set(
            project=project,
            extraction_run=run,
            hardware_group="HW-3",
            resolved=True,
            explicit_part=False,
            components=[linked],
            stats=stats,
        )

        component = HardwareSetComponent.objects.get(extraction_run=run)
        assert component.description == "Full mortise butt hinge"
        assert component.part_number == "BB1279"
        assert component.quantity == 3
        assert stats.hardware_components_written == 1

        # NFR-3: no value reaches an estimator without a provenance row, and the
        # row carries the page so the viewer can show it.
        provenance = FieldProvenance.objects.filter(hardware_component=component)
        assert provenance.count() == 5
        assert provenance.filter(field_name="part_number").first().page_number == 9
        assert provenance.filter(field_name="part_number").first().elements.count() == 1

    def test_raw_quantity_is_kept_when_it_cannot_be_typed(self, spec_elements):
        """
        A guessed quantity is a wrong quote line. ``PR`` stays raw and typed-null
        rather than being coerced to 1 (§5.7).
        """
        assert link_stage.parse_quantity("PR") is None
        assert link_stage.parse_quantity("2 EA") == 2
        assert link_stage.parse_quantity(None) is None


# ---------------------------------------------------------------------------
# Matching a component (§5.8 applied through the opening)
# ---------------------------------------------------------------------------

class TestComponentsInheritTheOpeningsHardConstraints:
    def test_a_component_on_a_rated_opening_never_matches_an_unrated_item(self):
        """
        The component itself says nothing about rating — a hardware schedule line
        carries no certification claim. The constraint comes from the door.
        """
        opening = OpeningFactory(fire_rating_minutes=90, fire_rating_absent=False)
        component = HardwareSetComponentFactory(project=opening.project)
        unrated = CatalogItemFactory(
            sku="UNRATED-1",
            description="Full mortise butt hinge",  # a perfect text match
            fire_rating_minutes=None,
        )

        criteria = MatchCriteria.from_component(component, opening)
        result = match_criteria(criteria, CatalogSnapshot.load())

        assert unrated.id not in {c.catalog_item_id for c in result.accepted}

    def test_the_same_set_on_two_openings_is_two_matching_problems(self):
        """
        HW-3 on a 90-minute door and HW-3 on an unrated door are not the same
        match, which is why components are matched per opening rather than once
        per set.
        """
        project = ProjectFactory()
        rated = OpeningFactory(project=project, door_number="R-1", fire_rating_minutes=90)
        unrated = OpeningFactory(
            project=project,
            door_number="U-1",
            fire_rating_raw=None,
            fire_rating_minutes=None,
            fire_rating_absent=True,
        )
        HardwareSetComponentFactory(
            project=project, extraction_run=rated.extraction_run, hardware_group="HW-3"
        )
        CatalogItemFactory(sku="RATED-90", fire_rating_minutes=90)
        CatalogItemFactory(sku="UNRATED", fire_rating_minutes=None)

        match_project(project, snapshot=CatalogSnapshot.load())

        rated_skus = {
            m.catalog_item.sku
            for m in Match.objects.filter(opening=rated, hardware_component__isnull=False)
        }
        unrated_skus = {
            m.catalog_item.sku
            for m in Match.objects.filter(opening=unrated, hardware_component__isnull=False)
        }
        assert "UNRATED" not in rated_skus, "a rated opening never takes unrated hardware"
        assert rated_skus != unrated_skus or not rated_skus

    def test_a_component_match_does_not_collide_with_the_door_match(self):
        """
        ``uniq_match_pair`` covers (opening, hardware_component, catalog_item) with
        NULLs treated as equal, so the door line and a component line can point at
        the same catalogue item without either overwriting the other.
        """
        project = ProjectFactory()
        opening = OpeningFactory(project=project)
        HardwareSetComponentFactory(
            project=project, extraction_run=opening.extraction_run, hardware_group="HW-3"
        )
        CatalogItemFactory(sku="SHARED", fire_rating_minutes=90)

        match_project(project, snapshot=CatalogSnapshot.load())

        assert Match.objects.filter(opening=opening, hardware_component__isnull=True).exists()
        assert Match.objects.filter(opening=opening, hardware_component__isnull=False).exists()
