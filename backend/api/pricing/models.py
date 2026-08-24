"""
Effective-dated reference data for the pricing engine (§7.5).

Everything here is **data with an effective date, not a constant in code**. A
hardcoded 0.08 tax rate becomes a wrong invoice the first time a rate changes
(§1.1 engineering note), and a margin sheet baked into Python cannot reproduce a
quote issued last March (§6.2 step 5).

Every table exposes ``effective_on(...)``, which is the *only* supported way to
read it. A bare ``.filter(...).first()`` returns an arbitrary row when more than
one effective date exists — that was a live defect in the previous pricing code
and it produced silently wrong prices.
"""

import uuid
from datetime import date

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from projects.models import TimestampedModel

from shared.enums import ProductTypeBand


class EffectiveDatedQuerySet(models.QuerySet):
    """Shared 'as of' lookup for every effective-dated reference table."""

    def as_of(self, on: date | None = None):
        """Rows already in force at ``on`` (default today), newest first."""
        return self.filter(effective_date__lte=on or date.today()).order_by("-effective_date")


class FinishCode(TimestampedModel):
    """
    Dual finish-nomenclature interpreter (NR-3, §1.3).

    Two naming systems are in simultaneous use and both must be interpreted.

    **US19 and US26D must never collapse to the same row.** Estimators flagged this
    explicitly: they are different satin finishes on different base metals mapping
    to different BHMA codes, and a matcher that treats "satin" as a fuzzy token will
    conflate them. That is why lookup here is exact, never fuzzy (§5.7).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    us_code = models.CharField(
        max_length=50, unique=True, db_index=True, help_text="Legacy US code, e.g. US26D."
    )
    bhma_code = models.CharField(
        max_length=50, db_index=True, help_text="BHMA numeric code, e.g. 626."
    )
    description = models.CharField(max_length=255)
    base_metal = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="brass / stainless / steel / nickel. Same base metal scores higher in matching.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["us_code", "bhma_code"], name="uniq_finish_pair")
        ]
        indexes = [models.Index(fields=["bhma_code"])]

    def __str__(self) -> str:
        return f"{self.us_code} / {self.bhma_code} - {self.description}"


class ThroatDepth(TimestampedModel):
    """
    Frame throat depth by wall type (§1.3).

    Five standard sizes cover the large majority; anything else routes to a
    manually entered custom value (cap ~10 total). **A table, not a hardcoded
    pick-list.**
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wall_type = models.CharField(max_length=100, unique=True, db_index=True)
    throat_depth_inches = models.DecimalField(max_digits=5, decimal_places=3)
    is_custom = models.BooleanField(
        default=False, help_text="True for manually entered values outside the five standards."
    )
    notes = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return f'{self.wall_type}: {self.throat_depth_inches}"'


class MarginBand(TimestampedModel):
    """
    Product-type margin bands (§1.5).

    Margin is applied as a **divisor, not a markup**: ``sale = cost / (1 - margin)``.
    Stable for 14 years, overridable per line.

    ``divisor`` is a stored *property*, not a column: a separate column can drift
    out of agreement with ``target_margin_pct`` and there is no way to tell which
    one is authoritative. One source of truth.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_type_band = models.CharField(max_length=50, choices=ProductTypeBand.choices())
    target_margin_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Fraction, e.g. 0.2700 for 27%.",
    )
    floor_margin_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=(
            "Below this, quote lines set below_floor_flag (FR-15). Build the FLAG; "
            "build no approval workflow — CBC deferred routing (C13/R4)."
        ),
    )
    effective_date = models.DateField(default=date.today, db_index=True)

    objects = EffectiveDatedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product_type_band", "effective_date"], name="uniq_margin_band_effective"
            ),
            models.CheckConstraint(
                condition=models.Q(target_margin_pct__lt=1),
                name="ck_margin_below_one",
            ),
        ]
        indexes = [models.Index(fields=["product_type_band", "-effective_date"])]

    @property
    def divisor(self):
        """``1 - margin``. Derived, so it can never disagree with the margin."""
        return 1 - self.target_margin_pct

    @classmethod
    def effective_on(cls, band: str, on: date | None = None) -> "MarginBand | None":
        """The band in force for ``band`` at ``on``. The only supported read path."""
        return cls.objects.filter(product_type_band=band).as_of(on).first()

    def __str__(self) -> str:
        return f"{self.product_type_band}: {self.target_margin_pct} (div {self.divisor})"


class VendorMultiplier(TimestampedModel):
    """
    List-price multipliers by vendor and negotiated tier (§6.2 step 2).

    ``cost = list_price * multiplier`` — e.g. Hager's tier yields ~71% off list, a
    0.29 multiplier. **MAP is never used as cost**: MAP governs advertising, not
    what CBC pays.

    ``source_sheet_version`` is part of the record because NFR-3 requires
    traceability to the multiplier tier *and* the sheet version that produced it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor_name = models.CharField(max_length=255, db_index=True)
    tier = models.CharField(max_length=100, blank=True, default="", help_text="e.g. 'Standard'.")
    multiplier = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="0.29 = 71% off list.",
    )
    source_sheet_version = models.CharField(
        max_length=255, blank=True, help_text="Which sheet this came from. Required by NFR-3."
    )
    effective_date = models.DateField(default=date.today, db_index=True)

    # -- the programme behind the number (Risk R5, NFR-10) --------------------
    # A multiplier on its own cannot tell an estimator whether to trust it. Risk
    # R5 is that stale prices quietly drive real quotes, and NFR-10 has no named
    # owner yet — so the guardrail available today is making the programme's own
    # dates and owner visible next to the figure they justify.
    sheet_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="The programme as purchasing refers to it, e.g. 'Hager L3 Program'.",
    )
    protected_until = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "The date CBC's cost is held to. Past it a mid-year list increase "
            "reaches the quote, so a lapsed protection is a reason to confirm "
            "before a proposal goes out — not merely a stale date."
        ),
    )
    steward = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Who owns this sheet. Blank is the honest answer while NFR-10 is open.",
    )
    reviewed_on = models.DateField(
        null=True, blank=True, help_text="When someone last checked this against the vendor."
    )
    note = models.TextField(blank=True, default="", help_text="What an estimator should know.")

    objects = EffectiveDatedQuerySet.as_manager()

    @property
    def is_stale(self) -> bool:
        """
        True when this programme should not be trusted without a check.

        Two independent reasons, and either is enough: the cost protection has
        lapsed, or nobody has reviewed the sheet inside the configured freshness
        window. Derived rather than stored — a stored flag is wrong the day after
        it is written, which is exactly when it matters.
        """
        from datetime import timedelta

        from shared.config import get_settings

        today = date.today()
        if self.protected_until is not None and self.protected_until < today:
            return True
        if self.reviewed_on is None:
            return False
        months = get_settings().cost_freshness_months
        return self.reviewed_on < today - timedelta(days=months * 30)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vendor_name", "tier", "effective_date"], name="uniq_multiplier_effective"
            )
        ]
        indexes = [models.Index(fields=["vendor_name", "-effective_date"])]

    @classmethod
    def effective_on(
        cls, vendor: str, tier: str | None = None, on: date | None = None
    ) -> "VendorMultiplier | None":
        """
        The multiplier in force for ``vendor`` at ``on``.

        ``tier=None`` means *any tier*, which is the normal case: CBC negotiates
        one tier per vendor, and the tier is a label on that relationship rather
        than something a quote line chooses. Defaulting to an empty-string tier
        matched nothing whenever a real tier was recorded, and silently fell
        through to quoting undiscounted list price.
        """
        queryset = cls.objects.filter(vendor_name=vendor)
        if tier is not None:
            queryset = queryset.filter(tier=tier)
        return queryset.as_of(on).first()

    def __str__(self) -> str:
        suffix = f" ({self.tier})" if self.tier else ""
        return f"{self.vendor_name}{suffix}: {self.multiplier}"


class TaxRate(TimestampedModel):
    """
    Sales tax by jurisdiction (§1.1).

    Tax applies **only in Ohio (~8%) and Kentucky (6.5%, border nexus)**. The other
    48 states and Canada are untaxed because the sale is to a GC or corporation,
    not the end customer.

    ``jurisdiction`` is a two-letter state code. Ohio's real rate is
    county-dependent and the quoted ~8% is a working figure, which is exactly why
    this is effective-dated reference data rather than a constant.
    """

    #: The only jurisdictions CBC has nexus in. A quote for anywhere else is
    #: untaxed, and that is a business rule, not a missing row.
    TAXABLE_JURISDICTIONS = ("OH", "KY")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jurisdiction = models.CharField(
        max_length=8, db_index=True, help_text="Two-letter state code, e.g. 'OH'."
    )
    rate_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Fraction, e.g. 0.0800 for 8%.",
    )
    description = models.CharField(max_length=255, blank=True)
    effective_date = models.DateField(default=date.today, db_index=True)

    objects = EffectiveDatedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["jurisdiction", "effective_date"], name="uniq_tax_rate_effective"
            )
        ]
        indexes = [models.Index(fields=["jurisdiction", "-effective_date"])]

    @classmethod
    def effective_on(cls, jurisdiction: str | None, on: date | None = None) -> "TaxRate | None":
        """
        The rate in force, or None when the jurisdiction is untaxed.

        Returns None for anything outside OH/KY without querying: an accidental row
        for another state must not be able to tax a quote that should be untaxed.
        """
        if not jurisdiction:
            return None
        code = jurisdiction.strip().upper()
        if code not in cls.TAXABLE_JURISDICTIONS:
            return None
        return cls.objects.filter(jurisdiction=code).as_of(on).first()

    def __str__(self) -> str:
        return f"{self.jurisdiction}: {self.rate_pct * 100}%"
