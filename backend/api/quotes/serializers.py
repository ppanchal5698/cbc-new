"""Serializers for quotes and quote lines (FR-7, FR-9, FR-16)."""

from decimal import Decimal

from catalog.serializers import CatalogItemSerializer
from rest_framework import serializers

from .models import Quote, QuoteLine, VendorRFQ


class QuoteLineSerializer(serializers.ModelSerializer):
    catalog_item_detail = CatalogItemSerializer(source="catalog_item", read_only=True)

    class Meta:
        model = QuoteLine
        fields = [
            "id", "quote", "opening", "match", "catalog_item", "catalog_item_detail",
            "hardware_component",
            "line_group", "description", "unit", "line_order",
            "quantity", "our_cost", "margin_pct",
            "cost_source", "cost_effective_date", "cost_is_stale", "p21_reference",
            "list_price", "multiplier", "vendor_multiplier", "multiplier_sheet_version",
            "adders", "total_adders",
            "margin_band", "margin_overridden", "margin_override_reason", "below_floor_flag",
            "sale_each", "extended", "subtotal",
            "is_direct_equal", "substitution_note", "needs_review",
            "created_at", "updated_at",
        ]
        # Only Quantity, Our Cost, and Margin are human-entered (§1.5).
        # Everything derived is computed by the pricing engine and PERSISTED —
        # recomputing on read would silently rewrite a quote's history (§6.2 step 5).
        read_only_fields = [
            "id", "catalog_item_detail", "sale_each", "extended", "subtotal",
            "cost_is_stale", "below_floor_flag", "created_at", "updated_at",
        ]


class QuoteLineWriteSerializer(serializers.ModelSerializer):
    """
    Estimator edits (FR-9): accept, edit, delete, or add lines.

    An override must carry a reason. That is enforced by a database check
    constraint as well as here, because an unexplained margin change on a
    customer-facing document is an audit failure (§6.2 step 3).
    """

    class Meta:
        model = QuoteLine
        fields = [
            "quote", "opening", "match", "catalog_item", "line_group", "description",
            "unit", "line_order", "quantity", "our_cost", "margin_pct",
            "cost_source", "cost_effective_date", "p21_reference",
            "list_price", "multiplier", "vendor_multiplier", "multiplier_sheet_version",
            "adders", "total_adders", "margin_band",
            "margin_overridden", "margin_override_reason",
            "is_direct_equal", "substitution_note", "needs_review",
        ]

    def validate(self, attrs):
        overridden = attrs.get(
            "margin_overridden", getattr(self.instance, "margin_overridden", False)
        )
        reason = attrs.get(
            "margin_override_reason", getattr(self.instance, "margin_override_reason", "")
        )
        if overridden and not (reason or "").strip():
            raise serializers.ValidationError(
                {"margin_override_reason": "a margin override must record why (§6.2 step 3)"}
            )
        margin = attrs.get("margin_pct", getattr(self.instance, "margin_pct", Decimal("0")))
        if margin is not None and margin >= 1:
            # sale_each = cost / (1 - margin) divides by zero at 100%.
            raise serializers.ValidationError(
                {"margin_pct": "margin must be below 100% — it is applied as a divisor"}
            )
        return attrs


class VendorRFQSerializer(serializers.ModelSerializer):
    class Meta:
        model = VendorRFQ
        fields = [
            "id", "quote_line", "vendor", "status", "request_notes",
            "requested_at", "requested_by", "returned_price", "returned_at",
            "price_may_be_stale", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "requested_at", "requested_by", "created_at", "updated_at"]


class QuoteSerializer(serializers.ModelSerializer):
    lines = QuoteLineSerializer(many=True, read_only=True)
    freight_display = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id", "project", "created_by", "status",
            "subtotal_sale", "freight_amount", "freight_display",
            "tax_jurisdiction", "tax_rate_applied", "tax_amount", "grand_total",
            "notes", "terms_version",
            "approved_by", "approved_at", "exported_at", "export_key", "exported_to_email",
            "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "created_by", "status", "subtotal_sale", "tax_rate_applied",
            "tax_amount", "grand_total", "approved_by", "approved_at",
            "exported_at", "export_key", "exported_to_email", "created_at", "updated_at",
        ]

    def get_freight_display(self, obj) -> str:
        """
        ``TBD`` unless an estimator entered a value.

        Freight is a line with a nullable amount, never a computed number: FR-7
        requires the line and CBC confirmed freight is generally not quoted at
        estimate stage (C11). Both requirements are satisfied by rendering the
        absence honestly.
        """
        return "TBD" if obj.freight_amount is None else f"{obj.freight_amount:.2f}"


class QuoteWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Quote
        fields = ["project", "freight_amount", "tax_jurisdiction", "notes"]


class QuoteApprovalSerializer(serializers.Serializer):
    """
    The NFR-1 gate.

    Approval is explicit and typed rather than a status PATCH, so it always carries
    the person who approved it and cannot be set as a side effect of another edit.
    """

    confirm = serializers.BooleanField(
        help_text="Must be true. The estimator is affirming that every line has been reviewed."
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")
