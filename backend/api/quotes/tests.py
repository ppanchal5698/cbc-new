"""
Pricing, assembly, review, and the approval gate (§6.2, FR-7, FR-9, FR-15, NFR-1).

The governing constraint, quoted from the requirements workbook:

    The estimator stays in control of every quote. The copilot drafts, sources,
    and calculates — it does not send.

The arithmetic tests assert **to the cent**, because that is the standard §12.2
sets for the Phase 4 golden file: a quote that disagrees with CBC's Excel workbook
by a penny is a quote an estimator will not trust.
"""

from datetime import date
from decimal import Decimal

import pytest
from factories import (
    CatalogItemFactory,
    MarginBandFactory,
    ProjectFactory,
    QuoteFactory,
    QuoteLineFactory,
    TaxRateFactory,
    VendorMultiplierFactory,
)
from rest_framework import status

from quotes.models import QuoteLine
from quotes.pricing_ops import (
    PricingError,
    ReferenceCache,
    assemble_quote,
    mark_staleness,
    price_line,
    resolve_cost,
    sum_adders,
)
from shared.enums import CostSource, LineGroup, QuoteStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def cache():
    return ReferenceCache(as_of=date(2026, 1, 1))


# ---------------------------------------------------------------------------
# The arithmetic (§1.5, §6.2 step 3)
# ---------------------------------------------------------------------------

class TestMarginAsDivisor:
    def test_sale_each_is_cost_divided_by_one_minus_margin(self, cache):
        """
        Margin is applied as a **divisor, not a markup**. Stable for 14 years.

        A markup would give 100 * 1.27 = 127.00, which is a different and wrong
        number.
        """
        line = QuoteLineFactory(
            our_cost=Decimal("100.0000"), margin_pct=Decimal("0.2700"), margin_overridden=True,
            margin_override_reason="fixed for this test",
        )
        price_line(line, cache=cache)
        assert line.sale_each == Decimal("136.9863")  # 100 / 0.73
        assert line.sale_each != Decimal("127.0000")

    def test_extended_is_sale_each_times_quantity(self, cache):
        line = QuoteLineFactory(
            our_cost=Decimal("100.0000"), margin_pct=Decimal("0.2700"), quantity=Decimal("3.00"),
            margin_overridden=True, margin_override_reason="fixed",
        )
        price_line(line, cache=cache)
        assert line.extended == Decimal("410.96")  # 136.9863 * 3, to the cent

    @pytest.mark.parametrize(
        "margin,divisor",
        [
            (Decimal("0.27"), Decimal("0.73")),
            (Decimal("0.35"), Decimal("0.65")),
            (Decimal("0.40"), Decimal("0.60")),
            (Decimal("0.25"), Decimal("0.75")),
        ],
    )
    def test_every_band_divides_correctly(self, cache, margin, divisor):
        line = QuoteLineFactory(
            our_cost=Decimal("200.0000"), margin_pct=margin,
            margin_overridden=True, margin_override_reason="band check",
        )
        price_line(line, cache=cache)
        expected = (Decimal("200") / divisor).quantize(Decimal("0.0001"))
        assert abs(line.sale_each - expected) <= Decimal("0.0001")

    def test_margin_of_one_hundred_percent_is_refused_at_both_layers(self, cache):
        """
        Defence in depth: the database refuses to store it, and the engine refuses
        to divide by zero if one ever reaches it in memory.
        """
        from django.db import IntegrityError, transaction

        quote = QuoteFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            QuoteLine.objects.create(
                quote=quote, margin_pct=Decimal("1.0000"),
                margin_overridden=True, margin_override_reason="invalid",
            )

        unsaved = QuoteLine(
            quote=quote, our_cost=Decimal("50.0000"), margin_pct=Decimal("1.0000"),
            quantity=Decimal("1.00"), margin_overridden=True, margin_override_reason="invalid",
        )
        with pytest.raises(PricingError):
            price_line(unsaved, cache=cache)

    def test_adders_are_added_before_the_divisor(self, cache):
        """
        NR-4: manual adders sit outside the base price book and are added on top.

        They form part of the cost basis, so the margin applies to them too.
        """
        line = QuoteLineFactory(
            our_cost=Decimal("100.0000"), margin_pct=Decimal("0.2700"),
            adders={"electrification": "40.00", "nrp_hinges": "10.00"},
            margin_overridden=True, margin_override_reason="adder check",
        )
        price_line(line, cache=cache)
        assert line.total_adders == Decimal("50.00")
        assert line.sale_each == Decimal("205.4795")  # 150 / 0.73

    def test_non_numeric_adder_is_ignored_not_crashed_on(self):
        line = QuoteLineFactory(adders={"good": "10.00", "bad": "see vendor"})
        assert sum_adders(line) == Decimal("10.00")


# ---------------------------------------------------------------------------
# Cost waterfall (§6.2 step 1)
# ---------------------------------------------------------------------------

class TestCostWaterfall:
    def test_priority_order_is_the_declared_order(self):
        assert [s.value for s in CostSource.waterfall()] == [
            "P21_LAST_PO", "DISTRIBUTOR_SHEET", "MFR_LIST", "VENDOR_RFQ", "MANUAL",
        ]
        assert CostSource.P21_LAST_PO.priority < CostSource.MFR_LIST.priority
        assert CostSource.MFR_LIST.priority < CostSource.MANUAL.priority

    def test_list_times_multiplier_produces_cost(self, cache):
        VendorMultiplierFactory(
            vendor_name="Hager", tier="Standard",
            multiplier=Decimal("0.2900"), effective_date=date(2024, 1, 1),
        )
        item = CatalogItemFactory(vendor="Hager", list_price=Decimal("100.00"))
        line = QuoteLineFactory(catalog_item=item, cost_source="MFR_LIST", our_cost=Decimal("0"))
        cost, source = resolve_cost(line, cache=cache)
        assert cost == Decimal("29.0000")
        assert source is CostSource.MFR_LIST
        # NFR-3: the sheet version travels onto the line, not just a reference.
        assert line.multiplier_sheet_version == "TEST-SHEET"

    def test_a_manual_cost_is_never_overwritten(self, cache):
        """
        MANUAL is first-class from day one, not a fallback (Risk R3).

        Re-sourcing an estimator's number would be the silent refresh NFR-10
        forbids.
        """
        item = CatalogItemFactory(vendor="Hager", list_price=Decimal("100.00"))
        VendorMultiplierFactory(vendor_name="Hager", tier="Standard", effective_date=date(2024, 1, 1))
        line = QuoteLineFactory(
            catalog_item=item, cost_source="MANUAL", our_cost=Decimal("42.5000")
        )
        cost, source = resolve_cost(line, cache=cache)
        assert cost == Decimal("42.5000")
        assert source is CostSource.MANUAL

    def test_missing_multiplier_flags_rather_than_quoting_list_silently(self, cache):
        item = CatalogItemFactory(vendor="UnknownVendor", list_price=Decimal("80.00"))
        line = QuoteLineFactory(catalog_item=item, cost_source="MFR_LIST", our_cost=Decimal("0"))
        cost, _ = resolve_cost(line, cache=cache)
        assert cost == Decimal("80.0000")
        assert line.needs_review is True

    def test_no_catalogue_item_routes_to_manual(self, cache):
        """NR-13: the estimator owns the long tail by design, not by failure."""
        line = QuoteLineFactory(catalog_item=None, cost_source="MFR_LIST", our_cost=Decimal("0"))
        _, source = resolve_cost(line, cache=cache)
        assert source is CostSource.MANUAL
        assert line.needs_review is True


class TestStaleness:
    def test_a_cost_beyond_the_window_is_marked_stale(self, settings):
        line = QuoteLineFactory(cost_effective_date=date(2020, 1, 1))
        assert mark_staleness(line, as_of=date(2026, 1, 1)) is True
        assert line.cost_is_stale is True

    def test_a_recent_cost_is_not_stale(self):
        line = QuoteLineFactory(cost_effective_date=date(2026, 1, 1))
        assert mark_staleness(line, as_of=date(2026, 2, 1)) is False

    def test_no_effective_date_is_not_asserted_stale(self):
        """Unknown is not the same as old; asserting either way would be a guess."""
        line = QuoteLineFactory(cost_effective_date=None)
        assert mark_staleness(line) is False

    def test_the_window_is_configuration_not_a_constant(self):
        from shared.config import get_settings

        assert get_settings().cost_freshness_months >= 1


# ---------------------------------------------------------------------------
# Margin floor (FR-15, C13 / Risk R4)
# ---------------------------------------------------------------------------

class TestMarginFloor:
    def test_below_floor_sets_the_flag(self, cache):
        MarginBandFactory(
            product_type_band="COMMODITY",
            target_margin_pct=Decimal("0.2700"),
            floor_margin_pct=Decimal("0.2000"),
            effective_date=date(2024, 1, 1),
        )
        item = CatalogItemFactory(product_type_band="COMMODITY")
        line = QuoteLineFactory(
            catalog_item=item, our_cost=Decimal("100.0000"), margin_pct=Decimal("0.1000"),
            margin_overridden=True, margin_override_reason="sourcing-driven, Wendy's",
        )
        price_line(line, cache=cache)
        assert line.below_floor_flag is True

    def test_at_or_above_floor_does_not_flag(self, cache):
        MarginBandFactory(
            product_type_band="COMMODITY",
            target_margin_pct=Decimal("0.2700"),
            floor_margin_pct=Decimal("0.2000"),
            effective_date=date(2024, 1, 1),
        )
        item = CatalogItemFactory(product_type_band="COMMODITY")
        line = QuoteLineFactory(catalog_item=item, our_cost=Decimal("100.0000"))
        price_line(line, cache=cache)
        assert line.below_floor_flag is False

    def test_no_approval_workflow_exists(self):
        """
        C13/R4: build the flag, build no workflow.

        FR-15 asks for routing; Open Item 14 defers it. Implementing routing would
        build something CBC deferred.
        """
        assert not hasattr(QuoteLine, "approval_status")
        assert not hasattr(QuoteLine, "approver")

    def test_an_override_without_a_reason_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction

        quote = QuoteFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            QuoteLine.objects.create(
                quote=quote, margin_overridden=True, margin_override_reason=""
            )


# ---------------------------------------------------------------------------
# Assembly (§6.2 step 4, FR-7)
# ---------------------------------------------------------------------------

class TestAssembly:
    def test_totals_are_stored_not_computed_on_read(self):
        """
        §6.2 step 5.

        A quote issued in March must reproduce identically in September after the
        margin and multiplier sheets have both changed.
        """
        quote = QuoteFactory(tax_jurisdiction=None)
        QuoteLineFactory(
            quote=quote, our_cost=Decimal("100.0000"), margin_pct=Decimal("0.2700"),
            quantity=Decimal("2.00"), margin_overridden=True, margin_override_reason="fixed",
        )
        assemble_quote(quote, as_of=date(2026, 1, 1))
        quote.refresh_from_db()
        assert quote.subtotal_sale == Decimal("273.97")
        assert quote.grand_total == Decimal("273.97")

        # The stored figure survives a reference-data change.
        MarginBandFactory(
            product_type_band="COMMODITY", target_margin_pct=Decimal("0.9000"),
            effective_date=date(2026, 6, 1),
        )
        quote.refresh_from_db()
        assert quote.grand_total == Decimal("273.97")

    def test_groups_get_their_own_subtotals(self):
        quote = QuoteFactory(tax_jurisdiction=None)
        for group, cost in [("DOOR", "100.0000"), ("RESTROOM_ACCESSORIES", "50.0000")]:
            QuoteLineFactory(
                quote=quote, line_group=group, our_cost=Decimal(cost),
                margin_pct=Decimal("0.2700"), margin_overridden=True,
                margin_override_reason="fixed",
            )
        assemble_quote(quote, as_of=date(2026, 1, 1))
        doors = quote.lines.filter(line_group="DOOR").first()
        accessories = quote.lines.filter(line_group="RESTROOM_ACCESSORIES").first()
        assert doors.subtotal == Decimal("136.99")
        assert accessories.subtotal == Decimal("68.49")

    def test_ohio_tax_is_applied(self):
        TaxRateFactory(jurisdiction="OH", rate_pct=Decimal("0.0800"), effective_date=date(2024, 1, 1))
        quote = QuoteFactory(tax_jurisdiction="OH")
        QuoteLineFactory(
            quote=quote, our_cost=Decimal("100.0000"), margin_pct=Decimal("0.2700"),
            margin_overridden=True, margin_override_reason="fixed",
        )
        assemble_quote(quote, as_of=date(2026, 1, 1))
        quote.refresh_from_db()
        assert quote.tax_rate_applied == Decimal("0.0800")
        assert quote.tax_amount == Decimal("10.96")   # 136.99 * 0.08
        assert quote.grand_total == Decimal("147.95")

    def test_a_non_nexus_state_is_untaxed(self):
        """Only OH and KY. The other 48 states and Canada are untaxed (§1.1)."""
        TaxRateFactory(jurisdiction="OH", rate_pct=Decimal("0.0800"), effective_date=date(2024, 1, 1))
        quote = QuoteFactory(tax_jurisdiction="TX")
        QuoteLineFactory(
            quote=quote, our_cost=Decimal("100.0000"), margin_pct=Decimal("0.2700"),
            margin_overridden=True, margin_override_reason="fixed",
        )
        assemble_quote(quote, as_of=date(2026, 1, 1))
        quote.refresh_from_db()
        assert quote.tax_amount == Decimal("0.00")
        assert quote.tax_rate_applied is None

    def test_freight_renders_tbd_when_not_entered(self, auth_client):
        """
        C11: freight is a line with a nullable amount, never a computed number.

        FR-7 requires the line; CBC confirmed freight is generally not quoted at
        estimate stage. Rendering the absence honestly satisfies both.
        """
        quote = QuoteFactory(freight_amount=None)
        body = auth_client.get(f"/api/quotes/{quote.id}/").data
        assert body["freight_display"] == "TBD"

    def test_freight_shows_the_entered_value(self, auth_client):
        quote = QuoteFactory(freight_amount=Decimal("250.00"))
        assert auth_client.get(f"/api/quotes/{quote.id}/").data["freight_display"] == "250.00"

    def test_freight_is_not_marked_up(self):
        """A margin on freight would invent a number CBC does not quote."""
        quote = QuoteFactory(tax_jurisdiction=None)
        line = QuoteLineFactory(
            quote=quote, line_group=LineGroup.FREIGHT.value,
            our_cost=Decimal("250.0000"), margin_pct=Decimal("0.2700"),
        )
        price_line(line)
        assert line.sale_each == Decimal("250.0000")


# ---------------------------------------------------------------------------
# The approval gate (NFR-1)
# ---------------------------------------------------------------------------

class TestApprovalGate:
    def test_a_new_quote_is_draft(self):
        assert QuoteFactory().status == QuoteStatus.DRAFT.value

    def test_approve_transitions_and_records_who(self, auth_client, user):
        quote = QuoteFactory(status="DRAFT")
        response = auth_client.post(
            f"/api/quotes/{quote.id}/approve/", {"confirm": True}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        quote.refresh_from_db()
        assert quote.status == QuoteStatus.APPROVED.value
        assert quote.approved_by_id == user.id
        assert quote.approved_at is not None

    def test_approve_refuses_while_a_line_needs_review(self, auth_client):
        """NFR-2: unmatched or low-confidence items are never silently accepted."""
        quote = QuoteFactory(status="DRAFT")
        QuoteLineFactory(quote=quote, needs_review=True)
        response = auth_client.post(
            f"/api/quotes/{quote.id}/approve/", {"confirm": True}, format="json"
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["lines_needing_review"] == 1
        quote.refresh_from_db()
        assert quote.status == QuoteStatus.DRAFT.value

    def test_approve_requires_explicit_confirmation(self, auth_client):
        quote = QuoteFactory(status="DRAFT")
        response = auth_client.post(
            f"/api/quotes/{quote.id}/approve/", {"confirm": False}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_refuses_an_unapproved_quote(self, auth_client):
        """There is no send path without an APPROVED transition."""
        quote = QuoteFactory(status="DRAFT")
        response = auth_client.post(f"/api/quotes/{quote.id}/export/", {}, format="json")
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "APPROVED" in response.data["detail"]

    def test_approving_twice_is_refused(self, auth_client):
        quote = QuoteFactory(status="APPROVED")
        response = auth_client.post(
            f"/api/quotes/{quote.id}/approve/", {"confirm": True}, format="json"
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_an_approved_quote_cannot_be_deleted(self, auth_client):
        quote = QuoteFactory(status="APPROVED")
        assert auth_client.delete(f"/api/quotes/{quote.id}/").status_code == 400


# ---------------------------------------------------------------------------
# Line editing (FR-9, FR-13)
# ---------------------------------------------------------------------------

class TestLineEditing:
    def test_lines_can_be_added(self, auth_client):
        quote = QuoteFactory()
        response = auth_client.post(
            "/api/quote-lines/",
            {
                "quote": str(quote.id), "line_group": "DOOR", "description": "Added by hand",
                "quantity": "2.00", "our_cost": "50.0000", "margin_pct": "0.2700",
                "cost_source": "MANUAL",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_editing_writes_a_feedback_row(self, auth_client):
        """FR-13: every review-UI edit writes a feedback row."""
        from feedback.models import Feedback

        line = QuoteLineFactory(our_cost=Decimal("100.0000"))
        auth_client.patch(
            f"/api/quote-lines/{line.id}/", {"our_cost": "80.0000"}, format="json"
        )
        record = Feedback.objects.get(entity_id=line.id, field_name="our_cost")
        assert record.value_after.startswith("80")

    def test_deleting_writes_a_feedback_row(self, auth_client):
        from feedback.models import Feedback

        line = QuoteLineFactory(description="Wrong item")
        auth_client.delete(f"/api/quote-lines/{line.id}/")
        assert Feedback.objects.filter(entity_id=line.id, field_name="__deleted__").exists()

    def test_an_override_without_a_reason_is_refused_by_the_api(self, auth_client):
        line = QuoteLineFactory()
        response = auth_client.patch(
            f"/api/quote-lines/{line.id}/",
            {"margin_overridden": True, "margin_override_reason": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_lines_are_frozen_once_the_quote_is_approved(self, auth_client):
        quote = QuoteFactory(status="APPROVED")
        line = QuoteLineFactory(quote=quote)
        response = auth_client.patch(
            f"/api/quote-lines/{line.id}/", {"quantity": "5.00"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Prior-quote reuse (FR-11)
# ---------------------------------------------------------------------------

class TestPriorQuoteSearch:
    def test_search_by_brand(self, auth_client):
        QuoteFactory(project=ProjectFactory(brand="McDonald's"))
        QuoteFactory(project=ProjectFactory(brand="Wendy's"))
        body = auth_client.get("/api/quotes/search/?brand=McDonald").data
        assert body["count"] == 1

    def test_search_is_case_insensitive(self, auth_client):
        QuoteFactory(project=ProjectFactory(brand="McDonald's"))
        assert auth_client.get("/api/quotes/search/?brand=mcdonald").data["count"] == 1

    def test_search_by_general_contractor(self, auth_client):
        QuoteFactory(project=ProjectFactory(general_contractor="Turner Construction"))
        QuoteFactory(project=ProjectFactory(general_contractor="Other GC"))
        assert auth_client.get("/api/quotes/search/?gc=Turner").data["count"] == 1

    def test_no_match_returns_empty(self, auth_client):
        QuoteFactory(project=ProjectFactory(brand="McDonald's"))
        assert auth_client.get("/api/quotes/search/?brand=Nobody").data["count"] == 0


class TestNoLegacyWeightField:
    def test_unit_weight_is_not_rebuilt(self):
        """§1.5: the legacy truck-loading field is confirmed obsolete."""
        assert not hasattr(QuoteLine, "unit_weight")
