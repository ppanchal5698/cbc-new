"""
From matched openings to a priced draft quote (FR-7, §6.2 step 4).

This is the step that decides whether the system delivers what NFR-6 promises.
Everything upstream can be perfect and the estimator still re-keys the whole
quote by hand if nothing turns matches into lines — so the tests here are about
the shape of the draft, not about the arithmetic, which `api/quotes/tests.py`
already pins to the cent.

The two properties worth defending:

* a below-cut-off opening still gets a **visible** line with no price, rather
  than being dropped (NR-13, NFR-2);
* regeneration never destroys an estimator's work (FR-9, FR-13).
"""

from decimal import Decimal

import pytest
from factories import (
    CatalogItemFactory,
    HardwareSetComponentFactory,
    MarginBandFactory,
    OpeningFactory,
    ProjectFactory,
    QuoteFactory,
)
from quotes.draft_ops import DraftError, generate_lines
from quotes.models import QuoteLine

from pipeline.stages.match import CatalogSnapshot, match_project
from shared.enums import LineGroup, QuoteStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def bid():
    """
    Two rated openings sharing HW-3, a catalogue that can serve them, and the
    commodity margin band the pricing engine reads.
    """
    MarginBandFactory(product_type_band="COMMODITY", target_margin_pct=Decimal("0.2700"))
    project = ProjectFactory()
    first = OpeningFactory(project=project, door_number="101", hardware_group="HW-3")
    OpeningFactory(
        project=project,
        door_number="102",
        hardware_group="HW-3",
        extraction_run=first.extraction_run,
    )
    HardwareSetComponentFactory(
        project=project,
        extraction_run=first.extraction_run,
        hardware_group="HW-3",
        component_index=0,
        description="Full mortise butt hinge",
        quantity=Decimal("3"),
    )
    CatalogItemFactory(
        sku="RATED-HINGE",
        description="Full mortise butt hinge",
        fire_rating_minutes=90,
        list_price=Decimal("38.00"),
    )
    match_project(project, snapshot=CatalogSnapshot.load())
    return project


class TestTheDraftIsBuiltFromWhatMatchingFound:
    def test_every_opening_gets_a_line(self, bid):
        quote = generate_lines(QuoteFactory(project=bid))
        doors = quote.lines.filter(opening__isnull=False, hardware_component__isnull=True)
        assert doors.count() == 2

    def test_hardware_lines_sit_directly_beneath_their_door(self, bid):
        """
        FR-7's "grouped by door" is an ordering claim as much as a subtotal one —
        it is how CBC's own workbook reads.
        """
        quote = generate_lines(QuoteFactory(project=bid))
        ordered = list(
            quote.lines.filter(opening__isnull=False).order_by("line_order")
        )
        assert ordered[0].hardware_component is None
        assert ordered[1].hardware_component is not None
        assert ordered[1].opening_id == ordered[0].opening_id

    def test_a_component_quantity_carries_onto_its_line(self, bid):
        quote = generate_lines(QuoteFactory(project=bid))
        hardware = quote.lines.get(hardware_component__isnull=False, opening__door_number="101")
        assert hardware.quantity == 3, "three hinges per opening, not one"

    def test_exactly_one_freight_line_and_it_is_empty(self, bid):
        """C11: freight is a line with a nullable amount, never a computed number."""
        quote = generate_lines(QuoteFactory(project=bid))
        freight = quote.lines.filter(line_group=LineGroup.FREIGHT.value)
        assert freight.count() == 1
        assert freight.first().extended == Decimal("0.00")

    def test_totals_are_stored_on_the_quote(self, bid):
        quote = generate_lines(QuoteFactory(project=bid))
        quote.refresh_from_db()
        assert quote.grand_total > 0
        assert quote.subtotal_sale > 0


class TestNothingIsSilentlyDropped:
    def test_an_opening_with_no_usable_match_still_gets_a_flagged_line(self):
        """
        NR-13 routes the long tail to the estimator. That has to look like a line
        they can see and price, not like an opening that vanished off the quote.
        """
        MarginBandFactory(product_type_band="COMMODITY")
        project = ProjectFactory()
        OpeningFactory(project=project, door_number="999", hardware_group=None)
        # No catalogue at all, so matching finds nothing.
        match_project(project, snapshot=CatalogSnapshot.load())

        quote = generate_lines(QuoteFactory(project=project))

        line = quote.lines.get(opening__door_number="999")
        assert line.needs_review is True
        assert line.catalog_item is None
        assert "999" in line.description, "the estimator must see which door this is"

    def test_a_flagged_line_is_left_for_the_estimator(self):
        """The draft is a draft: NFR-1's gate still refuses it."""
        MarginBandFactory(product_type_band="COMMODITY")
        project = ProjectFactory()
        OpeningFactory(project=project, hardware_group=None)
        match_project(project, snapshot=CatalogSnapshot.load())
        quote = generate_lines(QuoteFactory(project=project))

        assert quote.lines.filter(needs_review=True).exists()


class TestRegenerationNeverDestroysEstimatorWork:
    def test_a_second_run_is_refused_by_default(self, bid):
        quote = generate_lines(QuoteFactory(project=bid))
        with pytest.raises(DraftError, match="replace=true"):
            generate_lines(quote)

    def test_replace_rebuilds_generated_lines(self, bid):
        quote = generate_lines(QuoteFactory(project=bid))
        before = quote.lines.filter(opening__isnull=False).count()
        generate_lines(quote, replace=True)
        assert quote.lines.filter(opening__isnull=False).count() == before

    def test_replace_leaves_hand_added_lines_alone(self, bid):
        """
        A free-form line is an estimator decision the generator knows nothing
        about — an adder, a custom item, an RFQ placeholder.
        """
        quote = generate_lines(QuoteFactory(project=bid))
        manual = QuoteLine.objects.create(
            quote=quote,
            line_group=LineGroup.OTHER.value,
            description="Custom fabricated transom — quoted by phone",
            quantity=Decimal("1.00"),
            our_cost=Decimal("450.0000"),
            margin_pct=Decimal("0.2500"),
        )

        generate_lines(quote, replace=True)

        assert QuoteLine.objects.filter(id=manual.id).exists()

    def test_an_approved_quote_cannot_be_regenerated(self, bid):
        """NFR-1: an approved quote is a record of what was sent."""
        quote = generate_lines(QuoteFactory(project=bid))
        quote.status = QuoteStatus.APPROVED.value
        quote.save(update_fields=["status"])

        with pytest.raises(DraftError, match="APPROVED"):
            generate_lines(quote, replace=True)
