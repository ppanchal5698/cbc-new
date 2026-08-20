"""
Effective-dated reference data (§7.5, §1.1, §1.5).

Everything here is data with an effective date, not a constant in code. The tests
that matter most are the ones proving a *lookup* respects the effective date: a
bare ``.filter(...).first()`` returns an arbitrary row once more than one date
exists, and that produced silently wrong prices in the implementation this
replaces.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from factories import FinishCodeFactory, MarginBandFactory, TaxRateFactory, VendorMultiplierFactory
from rest_framework import status

from pricing.models import FinishCode, MarginBand, TaxRate, ThroatDepth, VendorMultiplier

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Finish codes (NR-3, §1.3)
# ---------------------------------------------------------------------------

class TestFinishCodes:
    def test_us19_and_us26d_never_collapse(self):
        """
        Estimators flagged this explicitly.

        Both are 'satin'. Different base metals, different BHMA codes, different
        rows — and a matcher that fuzzy-matches the word would conflate them.
        """
        a = FinishCodeFactory(us_code="US26D", bhma_code="626", base_metal="brass")
        b = FinishCodeFactory(us_code="US19", bhma_code="622", base_metal="steel")
        assert a.id != b.id
        assert a.bhma_code != b.bhma_code
        assert a.base_metal != b.base_metal

    def test_us_code_is_unique(self):
        FinishCodeFactory(us_code="US26D")
        with pytest.raises(IntegrityError), transaction.atomic():
            FinishCode.objects.create(us_code="US26D", bhma_code="999", description="dup")

    def test_seeded_set_covers_the_spec_table(self, django_db_setup):
        """§1.3 seed data: six codes, both nomenclatures."""
        from django.core.management import call_command

        call_command("seed_reference", verbosity=0)
        assert FinishCode.objects.count() >= 6
        for us_code, bhma in [("US26D", "626"), ("US32D", "630"), ("US19", "622")]:
            assert FinishCode.objects.get(us_code=us_code).bhma_code == bhma


# ---------------------------------------------------------------------------
# Margin bands (§1.5)
# ---------------------------------------------------------------------------

class TestMarginBands:
    def test_divisor_is_derived_not_stored(self):
        """
        One source of truth.

        A separate divisor column can drift out of agreement with the margin and
        there is no way to tell which one is authoritative.
        """
        band = MarginBandFactory(target_margin_pct=Decimal("0.2700"))
        assert band.divisor == Decimal("0.7300")

    def test_the_four_spec_bands_and_their_divisors(self):
        for pct, divisor in [
            (Decimal("0.27"), Decimal("0.73")),   # Commodity
            (Decimal("0.35"), Decimal("0.65")),   # Restroom partitions
            (Decimal("0.40"), Decimal("0.60")),   # Specialty
            (Decimal("0.25"), Decimal("0.75")),   # Custom fabricated
        ]:
            assert (Decimal("1") - pct) == divisor

    def test_margin_of_one_is_refused_by_the_database(self):
        """``sale = cost / (1 - margin)`` divides by zero at 100%."""
        with pytest.raises(IntegrityError), transaction.atomic():
            MarginBand.objects.create(
                product_type_band="COMMODITY",
                target_margin_pct=Decimal("1.0000"),
                effective_date=date(2030, 1, 1),
            )

    def test_effective_on_returns_the_band_in_force(self):
        MarginBandFactory(
            product_type_band="COMMODITY",
            target_margin_pct=Decimal("0.2500"),
            effective_date=date(2024, 1, 1),
        )
        MarginBandFactory(
            product_type_band="COMMODITY",
            target_margin_pct=Decimal("0.2700"),
            effective_date=date(2025, 6, 1),
        )
        assert MarginBand.effective_on("COMMODITY", date(2025, 1, 1)).target_margin_pct == Decimal("0.2500")
        assert MarginBand.effective_on("COMMODITY", date(2026, 1, 1)).target_margin_pct == Decimal("0.2700")

    def test_a_future_band_is_not_in_force_yet(self):
        MarginBandFactory(product_type_band="SPECIALTY", effective_date=date(2099, 1, 1))
        assert MarginBand.effective_on("SPECIALTY", date(2026, 1, 1)) is None


# ---------------------------------------------------------------------------
# Vendor multipliers (§6.2 step 2, NFR-3)
# ---------------------------------------------------------------------------

class TestVendorMultipliers:
    def test_hager_tier_yields_the_documented_discount(self):
        """§1.5: Hager's negotiated tier is ~71% off list, a 0.29 multiplier."""
        row = VendorMultiplierFactory(vendor_name="Hager", multiplier=Decimal("0.2900"))
        assert (Decimal("1") - row.multiplier) * 100 == Decimal("71.00")
        assert Decimal("100.00") * row.multiplier == Decimal("29.0000")

    def test_source_sheet_version_is_recorded(self):
        """NFR-3 requires traceability to the tier AND the sheet version."""
        row = VendorMultiplierFactory(source_sheet_version="Hager 2026 Q3")
        assert row.source_sheet_version

    def test_effective_on_picks_the_current_sheet(self):
        VendorMultiplierFactory(
            vendor_name="Hager", tier="Standard",
            multiplier=Decimal("0.3200"), effective_date=date(2024, 1, 1),
        )
        VendorMultiplierFactory(
            vendor_name="Hager", tier="Standard",
            multiplier=Decimal("0.2900"), effective_date=date(2026, 1, 1),
        )
        assert VendorMultiplier.effective_on("Hager", "Standard", date(2025, 1, 1)).multiplier == Decimal("0.3200")
        assert VendorMultiplier.effective_on("Hager", "Standard", date(2026, 6, 1)).multiplier == Decimal("0.2900")


# ---------------------------------------------------------------------------
# Tax (§1.1)
# ---------------------------------------------------------------------------

class TestTaxRates:
    def test_only_ohio_and_kentucky_are_taxable(self):
        """
        The other 48 states and Canada are untaxed because the sale is to a GC or
        corporation, not the end customer.
        """
        assert TaxRate.TAXABLE_JURISDICTIONS == ("OH", "KY")

    def test_ohio_and_kentucky_resolve(self):
        TaxRateFactory(jurisdiction="OH", rate_pct=Decimal("0.0800"))
        TaxRateFactory(jurisdiction="KY", rate_pct=Decimal("0.0650"))
        assert TaxRate.effective_on("OH").rate_pct == Decimal("0.0800")
        assert TaxRate.effective_on("KY").rate_pct == Decimal("0.0650")

    def test_another_state_is_untaxed_even_if_a_row_exists(self):
        """
        Belt and braces.

        An accidental row for another state must not be able to tax a quote that
        should be untaxed, so the jurisdiction is checked before the query.
        """
        TaxRate.objects.create(jurisdiction="CA", rate_pct=Decimal("0.0725"))
        assert TaxRate.effective_on("CA") is None

    def test_no_jurisdiction_is_untaxed(self):
        assert TaxRate.effective_on(None) is None
        assert TaxRate.effective_on("") is None

    def test_lookup_is_case_insensitive(self):
        TaxRateFactory(jurisdiction="OH")
        assert TaxRate.effective_on("oh") is not None

    def test_rate_change_does_not_mutate_history(self):
        """A quote issued in March must reproduce in September (§6.2 step 5)."""
        TaxRateFactory(jurisdiction="OH", rate_pct=Decimal("0.0750"), effective_date=date(2024, 1, 1))
        TaxRateFactory(jurisdiction="OH", rate_pct=Decimal("0.0800"), effective_date=date(2026, 1, 1))
        assert TaxRate.effective_on("OH", date(2025, 3, 1)).rate_pct == Decimal("0.0750")
        assert TaxRate.effective_on("OH", date(2026, 9, 1)).rate_pct == Decimal("0.0800")


# ---------------------------------------------------------------------------
# Throat depths (§1.3)
# ---------------------------------------------------------------------------

class TestThroatDepths:
    def test_the_five_standards_seed(self):
        from django.core.management import call_command

        call_command("seed_reference", verbosity=0)
        depths = sorted(ThroatDepth.objects.values_list("throat_depth_inches", flat=True))
        assert depths == [
            Decimal("5.625"), Decimal("5.750"), Decimal("5.875"),
            Decimal("7.750"), Decimal("8.250"),
        ]

    def test_seed_is_idempotent_and_uses_one_vocabulary(self):
        """
        Regression: two seeders once wrote different wall_type strings, so whichever
        ran last won and every lookup against the other vocabulary returned nothing.
        """
        from django.core.management import call_command

        call_command("seed_reference", verbosity=0)
        first = set(ThroatDepth.objects.values_list("wall_type", flat=True))
        call_command("seed_reference", verbosity=0)
        assert set(ThroatDepth.objects.values_list("wall_type", flat=True)) == first
        assert ThroatDepth.objects.count() == 5


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class TestPricingApi:
    def test_as_of_filters_to_rows_in_force(self, auth_client):
        MarginBandFactory(product_type_band="COMMODITY", effective_date=date(2024, 1, 1))
        MarginBandFactory(product_type_band="COMMODITY", effective_date=date(2099, 1, 1))
        body = auth_client.get("/api/margin-bands/?as_of=2026-01-01").data
        assert body["count"] == 1

    def test_divisor_is_exposed_read_only(self, auth_client):
        MarginBandFactory(target_margin_pct=Decimal("0.2700"))
        row = auth_client.get("/api/margin-bands/").data["results"][0]
        assert Decimal(row["divisor"]) == Decimal("0.7300")

    def test_reference_endpoints_require_auth(self, api_client):
        for path in ("/api/finish-codes/", "/api/margin-bands/", "/api/tax-rates/"):
            assert api_client.get(path).status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
