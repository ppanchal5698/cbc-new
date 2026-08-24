"""
The central reference library (FR-3, §7.5).

Explicitly **not per-project**: this is the fix for the Excel-workbook-per-job
status quo, where hardware sets and standard line items lived inside whichever job
file they were last used in.
"""

import uuid

from django.db import models
from projects.models import TimestampedModel

from shared.enums import Handing, LineGroup, ProductTypeBand


class CatalogItem(TimestampedModel):
    """
    One purchasable item.

    Matching treats ``fire_rating_minutes`` and ``handing`` as **hard** constraints
    (§6.1): rated hardware is a distinct certified product line, and handed parts
    are separate SKUs. Neither is a scored similarity.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    vendor = models.CharField(max_length=255, db_index=True)
    series = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="e.g. Hager 3400 vs 3500 — ANSI/BHMA Grade 1 vs Grade 2. Not interchangeable.",
    )
    sku = models.CharField(max_length=255, db_index=True)
    part_number = models.CharField(
        max_length=255, null=True, blank=True, help_text="Explicit manufacturer part."
    )
    description = models.TextField()

    list_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Manufacturer list. Multiplied by the vendor tier to reach cost (§6.2 step 2).",
    )
    list_price_effective_date = models.DateField(null=True, blank=True)
    list_price_sheet_version = models.CharField(max_length=255, blank=True)

    product_type_band = models.CharField(
        max_length=50,
        choices=ProductTypeBand.choices(),
        default=ProductTypeBand.COMMODITY.value,
        help_text="Drives the margin band.",
    )
    line_group = models.CharField(
        max_length=50, choices=LineGroup.choices(), default=LineGroup.OTHER.value
    )
    csi_division = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        db_index=True,
        help_text="08 / 09 / 10. A Division 10 accessory never matches a Division 08 opening.",
    )

    finish_code = models.ForeignKey(
        "pricing.FinishCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_items",
    )
    fire_rating_minutes = models.IntegerField(
        null=True, blank=True, help_text="20/45/60/90; null means unrated. HARD constraint."
    )
    handing = models.CharField(
        max_length=10,
        choices=Handing.choices(),
        null=True,
        blank=True,
        help_text="Null means the item is not handed. HARD constraint when set.",
    )

    is_stock = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "The top-10 list, extendable to ~20 (NR-6). NR-13: automate stock and "
            "top-N items; beyond that a clear MANUAL cut-off. The estimator owns the "
            "long tail by design, not by failure."
        ),
    )
    is_active = models.BooleanField(default=True)

    p21_item_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "NULLABLE ON PURPOSE (Risk R3): P21 item IDs diverge from manufacturer "
            "part numbers and semi-custom items will not match cleanly. The system "
            "never auto-accepts a cost match on part-number similarity alone."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["vendor", "sku"], name="uniq_catalog_vendor_sku")
        ]
        indexes = [
            models.Index(fields=["vendor", "series"]),
            models.Index(fields=["is_stock", "is_active"]),
            models.Index(fields=["csi_division", "product_type_band"]),
            models.Index(fields=["part_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.vendor} {self.sku}: {self.description[:50]}"


class CatalogItemXref(TimestampedModel):
    """
    The same physical item as another manufacturer numbers it (§1.4).

    Restroom accessories are the case this exists for: a specification names a
    Bobrick part, CBC quotes the ASI or Bradley equivalent, and the estimator is
    the only place that mapping currently lives. Searching a Bobrick number and
    finding the ASI equivalent is the difference between pricing the line and
    phoning someone.

    Deliberately **not** a substitution decision. §1.4 is explicit that choosing a
    direct equal is estimator judgment, not a rule the system applies: this table
    says "these are the same item to a manufacturer", it does not say "quote that
    one instead".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    catalog_item = models.ForeignKey(
        CatalogItem, on_delete=models.CASCADE, related_name="cross_references"
    )
    brand = models.CharField(max_length=255, db_index=True, help_text="e.g. 'ASI', 'Bradley'.")
    part_number = models.CharField(max_length=255, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["catalog_item", "brand", "part_number"], name="uniq_xref_per_item"
            )
        ]
        indexes = [models.Index(fields=["part_number"])]
        ordering = ["brand"]

    def __str__(self) -> str:
        return f"{self.brand} {self.part_number}"
