"""
Quotes, quote lines, and the vendor-RFQ loop (§7.5, FR-7, FR-15, FR-16).

Two rules govern this module and both are load-bearing:

1. **Only three fields per line are human-entered** — Quantity, Our Cost, Margin.
   Everything else derives (§1.5).
2. **Derived money is stored, not computed on read** (§6.2 step 5). A quote issued
   in March must reproduce identically in September after the margin sheet and the
   multiplier sheets have both changed. Recomputing on read silently rewrites
   history.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from projects.models import Project, TimestampedModel

from shared.enums import CostSource, LineGroup, QuoteStatus, VendorRFQStatus


class Quote(TimestampedModel):
    """
    One draft or approved quote for a project.

    ``status`` is the NFR-1 hard gate: there is no export path that does not pass
    through an APPROVED transition. The copilot drafts, sources, and calculates —
    it does not send.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="quotes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="quotes_created",
    )

    status = models.CharField(
        max_length=50,
        choices=QuoteStatus.choices(),
        default=QuoteStatus.DRAFT.value,
        db_index=True,
    )

    # -- stored totals (§6.2 step 5) ------------------------------------------
    subtotal_sale = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    freight_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "NULL renders as 'TBD'. Freight is a line with a nullable amount, never a "
            "computed number (C11): FR-7 requires the line, and CBC confirmed freight "
            "is generally not quoted at estimate stage."
        ),
    )
    tax_jurisdiction = models.CharField(
        max_length=8,
        null=True,
        blank=True,
        help_text="Two-letter state code. Only OH and KY are taxable (§1.1).",
    )
    tax_rate_applied = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="The rate actually used, stored so the quote reproduces after a rate change.",
    )
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    notes = models.TextField(blank=True)
    terms_version = models.CharField(
        max_length=50, blank=True, help_text="Standard commercial terms applied at export (FR-10)."
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotes_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    export_key = models.CharField(
        max_length=1024, null=True, blank=True, help_text="Rendered PDF in the derived bucket."
    )
    exported_to_email = models.EmailField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Captured initiator, never a group inbox (FR-10).",
    )

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Quote {self.id} ({self.status})"


class QuoteLine(TimestampedModel):
    """
    One line on a quote (§7.5).

    ``sale_each``, ``extended``, and ``subtotal`` are persisted columns rather than
    properties for the reason in the module docstring. There is deliberately **no
    ``unit_weight``** column: that legacy field (originally for truck-loading) is
    confirmed obsolete and is not rebuilt.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="lines")

    # All nullable: free-form lines, the accessories block, and the freight line
    # have no opening, no match, and no catalogue item.
    opening = models.ForeignKey(
        "openings.Opening", on_delete=models.SET_NULL, null=True, blank=True, related_name="quote_lines"
    )
    match = models.ForeignKey(
        "openings.Match", on_delete=models.SET_NULL, null=True, blank=True, related_name="quote_lines"
    )
    catalog_item = models.ForeignKey(
        "catalog.CatalogItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="quote_lines"
    )

    line_group = models.CharField(
        max_length=50, choices=LineGroup.choices(), default=LineGroup.DOOR.value, db_index=True
    )
    description = models.TextField(blank=True, default="")
    unit = models.CharField(max_length=50, default="EA")
    line_order = models.IntegerField(default=0)

    # -- the three human-entered fields (§1.5) --------------------------------
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("1.00"))
    our_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Human-entered or sourced through the waterfall.",
    )
    margin_pct = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.0000"), help_text="Human-editable."
    )

    # -- cost provenance (§6.2 step 1) ----------------------------------------
    cost_source = models.CharField(
        max_length=50,
        choices=CostSource.choices(),
        default=CostSource.MANUAL.value,
        help_text="Which waterfall path produced this cost. MANUAL is first-class (Risk R3).",
    )
    cost_effective_date = models.DateField(null=True, blank=True)
    cost_is_stale = models.BooleanField(
        default=False,
        help_text=(
            "Computed against COST_FRESHNESS_MONTHS (configurable, not hardcoded). "
            "Surfaces NR-2's 'price may be out of date - refresh' prompt. There is NO "
            "automatic silent refresh: a price that changes underneath an estimator "
            "without their knowledge is exactly the failure NFR-10 is about."
        ),
    )
    p21_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="The matched P21 record, always surfaced so the estimator can reject it (R3).",
    )

    # -- list x multiplier path (§6.2 step 2) ---------------------------------
    list_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    multiplier = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Copied onto the line, not just referenced, so the quote reproduces later.",
    )
    vendor_multiplier = models.ForeignKey(
        "pricing.VendorMultiplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_lines",
    )
    multiplier_sheet_version = models.CharField(
        max_length=255, blank=True, help_text="NFR-3 requires the sheet version AND the tier."
    )
    adders = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Manual adders outside the base price book (NR-4): electrification, "
            "non-removable-pin hinges, premium and lead-time finishes."
        ),
    )
    total_adders = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # -- margin (§6.2 step 3) --------------------------------------------------
    margin_band = models.ForeignKey(
        "pricing.MarginBand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_lines",
        help_text="The default this line's margin came from.",
    )
    margin_overridden = models.BooleanField(default=False)
    margin_override_reason = models.TextField(
        blank=True, help_text="e.g. the confirmed sourcing-driven Wendy's case."
    )
    below_floor_flag = models.BooleanField(
        default=False,
        db_index=True,
        help_text="FR-15: the FLAG, not the workflow. CBC deferred approval routing (C13/R4).",
    )

    # -- derived money, stored (§6.2 step 5) ----------------------------------
    sale_each = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0.0000"))
    extended = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    subtotal = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Group subtotal snapshot at the time of pricing.",
    )

    is_direct_equal = models.BooleanField(default=False)
    substitution_note = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["quote", "line_group", "line_order"]),
            models.Index(fields=["quote", "needs_review"]),
        ]
        ordering = ["quote", "line_group", "line_order"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(margin_pct__lt=1),
                name="ck_line_margin_below_one",
            ),
            # An override without a reason is an unexplained price change on a
            # customer-facing document (§6.2 step 3).
            models.CheckConstraint(
                condition=models.Q(margin_overridden=False)
                | ~models.Q(margin_override_reason=""),
                name="ck_override_requires_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.line_group}: {self.description[:40]}"


class VendorRFQ(TimestampedModel):
    """
    The vendor-RFQ loop (FR-16, §7.5).

    For large, custom, non-stock, or first-time items — a 9-foot door, an unusual
    prep, an option not sold in years — CBC requests a live quote. Slower path:
    request out, wait, enter by hand.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote_line = models.ForeignKey(QuoteLine, on_delete=models.CASCADE, related_name="rfqs")
    vendor = models.CharField(max_length=255)
    status = models.CharField(
        max_length=50, choices=VendorRFQStatus.choices(), default=VendorRFQStatus.REQUESTED.value
    )

    requested_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vendor_rfqs",
    )
    request_notes = models.TextField(blank=True)

    returned_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    price_may_be_stale = models.BooleanField(
        default=False, help_text="Drives NR-2's 'price may be out of date - refresh' prompt."
    )

    class Meta:
        indexes = [models.Index(fields=["quote_line", "status"])]

    def __str__(self) -> str:
        return f"RFQ {self.vendor} ({self.status})"
