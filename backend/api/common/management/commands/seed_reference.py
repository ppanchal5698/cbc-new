"""
Seed effective-dated reference data (§8.5 ``make seed``).

**This is the only seeder.** There were previously two — a management command and
a standalone script — carrying *different* ``ThroatDepth.wall_type`` strings and
different ``TaxRate.jurisdiction`` keys ('Ohio' vs 'OH'). Whichever ran last won,
and every lookup written against the other vocabulary silently returned nothing:
a quote with no tax and no throat depth, with no error anywhere. One vocabulary,
one file.

Data source is §1.3 and §1.5 of the specification. Everything written here is
effective-dated: re-running with a later ``--effective-date`` adds a new row
rather than mutating history, which is what lets a quote issued in March
reproduce in September (§6.2 step 5).
"""

from datetime import date
from decimal import Decimal

from catalog.models import CatalogItem
from django.core.management.base import BaseCommand
from django.db import transaction
from pricing.models import FinishCode, MarginBand, TaxRate, ThroatDepth, VendorMultiplier

# ---------------------------------------------------------------------------
# §1.3 — finish code normalisation (NR-3)
# ---------------------------------------------------------------------------
# US19 and US26D must never collapse to the same row. They are different satin
# finishes on different base metals mapping to different BHMA codes; a matcher
# that treats "satin" as a fuzzy token will conflate them. base_metal is stored
# so matching can score "same base metal" without ever fuzzy-matching the name.
FINISH_CODES = [
    ("US26D", "626", "Satin chrome on brass - most common interior commercial", "brass"),
    ("US26", "625", "Bright polished chrome", "brass"),
    ("US32D", "630", "Satin stainless - most common exit device / hinge / exterior", "stainless"),
    ("US32", "629", "Bright stainless", "stainless"),
    ("US19", "622", "Flat black", "steel"),
    ("US15", "619", "Satin nickel", "nickel"),
]

# ---------------------------------------------------------------------------
# §1.3 — frame throat depth by wall type
# ---------------------------------------------------------------------------
# Five standard sizes cover the large majority; anything else routes to a
# manually entered custom value. A table, not a hardcoded pick-list.
THROAT_DEPTHS = [
    ("Half-inch drywall", Decimal("5.625"), "Common at McDonald's-type builds"),
    ("Masonry", Decimal("5.750"), ""),
    ("Drywall (alternate spec)", Decimal("5.875"), ""),
    ("Wood-frame variant", Decimal("7.750"), ""),
    ('6" metal stud with 5/8" drywall', Decimal("8.250"), ""),
]

# ---------------------------------------------------------------------------
# §1.5 — margin bands, applied as a divisor
# ---------------------------------------------------------------------------
# (band, target, floor). Floors seed equal to target: CBC reports no margin
# deviation today (Open Item 14), so nothing is below floor until a steward sets
# a real floor. Build the flag, build no approval workflow (C13/R4).
MARGIN_BANDS = [
    ("COMMODITY", Decimal("0.2700"), Decimal("0.2700")),
    ("RESTROOM_PARTITIONS", Decimal("0.3500"), Decimal("0.3500")),
    ("SPECIALTY", Decimal("0.4000"), Decimal("0.4000")),
    ("CUSTOM_FABRICATED", Decimal("0.2500"), Decimal("0.2500")),
]

# ---------------------------------------------------------------------------
# §1.4 / §6.2 — vendor multipliers
# ---------------------------------------------------------------------------
# Hager's negotiated tier yields ~71% off list, a 0.29 multiplier (§1.5). The
# others are PLACEHOLDERS pending the real price sheets; source_sheet_version
# says so explicitly rather than presenting a guess as a negotiated rate.
VENDOR_MULTIPLIERS = [
    ("Hager", "Standard", Decimal("0.2900"), "SEED-PLACEHOLDER: confirm against Hager sheet"),
    ("Allegion", "Standard", Decimal("0.3200"), "SEED-PLACEHOLDER: confirm against Banner/SecLock"),
    ("Pemko", "Standard", Decimal("0.3500"), "SEED-PLACEHOLDER"),
    ("National Guard", "Standard", Decimal("0.3800"), "SEED-PLACEHOLDER"),
    ("Rockwood", "Standard", Decimal("0.3800"), "SEED-PLACEHOLDER"),
    ("Cal-Royal", "Standard", Decimal("0.4000"), "SEED-PLACEHOLDER"),
    ("Bobrick", "Standard", Decimal("0.4500"), "SEED-PLACEHOLDER"),
    ("Bradley", "Standard", Decimal("0.4500"), "SEED-PLACEHOLDER"),
    ("ASI", "Standard", Decimal("0.4500"), "SEED-PLACEHOLDER"),
]

# ---------------------------------------------------------------------------
# §1.1 — sales tax
# ---------------------------------------------------------------------------
# Ohio (~8%, county-dependent) and Kentucky (6.5%, border nexus) ONLY. The other
# 48 states and Canada are untaxed because the sale is to a GC or corporation,
# not the end customer. Keys are two-letter state codes; TaxRate.effective_on
# refuses anything else outright.
TAX_RATES = [
    ("OH", Decimal("0.0800"), "Ohio - county-dependent; 8% is a working figure"),
    ("KY", Decimal("0.0650"), "Kentucky - border nexus"),
]


class Command(BaseCommand):
    help = "Seed effective-dated reference data: finish codes, throat depths, margin bands, vendor multipliers, tax rates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--effective-date",
            default=None,
            help="ISO date for the effective-dated rows (default: today).",
        )
        parser.add_argument(
            "--with-sample-catalog",
            action="store_true",
            help=(
                "Also seed a handful of catalogue items so the matching engine can be "
                "exercised. NOT production data: NR-6 (CBC's top-10 stock list per "
                "product type) is outstanding and blocks Phase 3 go-live."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        effective = (
            date.fromisoformat(options["effective_date"])
            if options["effective_date"]
            else date.today()
        )

        self._seed_finish_codes()
        self._seed_throat_depths()
        self._seed_margin_bands(effective)
        self._seed_vendor_multipliers(effective)
        self._seed_tax_rates(effective)
        if options["with_sample_catalog"]:
            self._seed_sample_catalog()

        self.stdout.write(self.style.SUCCESS(f"Reference data seeded (effective {effective})."))

    # -- individual tables --------------------------------------------------

    def _seed_finish_codes(self):
        for us, bhma, description, metal in FINISH_CODES:
            FinishCode.objects.update_or_create(
                us_code=us,
                defaults={"bhma_code": bhma, "description": description, "base_metal": metal},
            )
        self.stdout.write(f"  finish_codes         {len(FINISH_CODES):>3}")

    def _seed_throat_depths(self):
        for wall_type, depth, notes in THROAT_DEPTHS:
            ThroatDepth.objects.update_or_create(
                wall_type=wall_type,
                defaults={"throat_depth_inches": depth, "is_custom": False, "notes": notes},
            )
        self.stdout.write(f"  throat_depths        {len(THROAT_DEPTHS):>3}")

    def _seed_margin_bands(self, effective):
        for band, target, floor in MARGIN_BANDS:
            MarginBand.objects.update_or_create(
                product_type_band=band,
                effective_date=effective,
                defaults={"target_margin_pct": target, "floor_margin_pct": floor},
            )
        self.stdout.write(f"  margin_bands         {len(MARGIN_BANDS):>3}")

    def _seed_vendor_multipliers(self, effective):
        for vendor, tier, multiplier, sheet in VENDOR_MULTIPLIERS:
            VendorMultiplier.objects.update_or_create(
                vendor_name=vendor,
                tier=tier,
                effective_date=effective,
                defaults={"multiplier": multiplier, "source_sheet_version": sheet},
            )
        self.stdout.write(f"  vendor_multipliers   {len(VENDOR_MULTIPLIERS):>3}")

    def _seed_tax_rates(self, effective):
        for jurisdiction, rate, description in TAX_RATES:
            TaxRate.objects.update_or_create(
                jurisdiction=jurisdiction,
                effective_date=effective,
                defaults={"rate_pct": rate, "description": description},
            )
        self.stdout.write(f"  tax_rates            {len(TAX_RATES):>3}  (OH and KY only)")

    def _seed_sample_catalog(self):
        """
        A minimal, deliberately obvious sample library.

        Chosen to exercise the hard constraints: a rated and an unrated hinge, an
        LH and an RH exit device, and a Division 10 accessory that must never match
        a Division 08 opening.
        """
        satin_chrome = FinishCode.objects.filter(us_code="US26D").first()
        satin_stainless = FinishCode.objects.filter(us_code="US32D").first()
        samples = [
            # vendor, sku, series, description, list, band, div, rating, handing, finish, stock
            ("Hager", "BB1279-US26D", "1279", "Full mortise butt hinge 4.5x4.5", Decimal("38.00"),
             "COMMODITY", "08", None, None, satin_chrome, True),
            ("Hager", "BB1168-90-US26D", "1168", "Fire-rated full mortise hinge 4.5x4.5", Decimal("52.00"),
             "COMMODITY", "08", 90, None, satin_chrome, True),
            ("Allegion", "VD-99-EO-LH-US32D", "99", "Von Duprin 99 rim exit device, LH", Decimal("1240.00"),
             "COMMODITY", "08", 90, "LH", satin_stainless, True),
            ("Allegion", "VD-99-EO-RH-US32D", "99", "Von Duprin 99 rim exit device, RH", Decimal("1240.00"),
             "COMMODITY", "08", 90, "RH", satin_stainless, True),
            ("Bobrick", "B-6806x36", "B-680", "Grab bar 36in stainless", Decimal("64.00"),
             "RESTROOM_PARTITIONS", "10", None, None, satin_stainless, True),
        ]
        for vendor, sku, series, desc, price, band, div, rating, handing, finish, stock in samples:
            CatalogItem.objects.update_or_create(
                vendor=vendor,
                sku=sku,
                defaults={
                    "series": series,
                    "description": desc,
                    "list_price": price,
                    "product_type_band": band,
                    "csi_division": div,
                    "fire_rating_minutes": rating,
                    "handing": handing,
                    "finish_code": finish,
                    "is_stock": stock,
                    "line_group": "RESTROOM_ACCESSORIES" if div == "10" else "DOOR",
                    "list_price_sheet_version": "SEED-PLACEHOLDER",
                },
            )
        self.stdout.write(
            self.style.WARNING(
                f"  catalog_items        {len(samples):>3}  SAMPLE ONLY - NR-6 blocks Phase 3 go-live"
            )
        )
