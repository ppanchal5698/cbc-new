"""Serializers for effective-dated reference data (§7.5)."""

from rest_framework import serializers

from .models import FinishCode, MarginBand, TaxRate, ThroatDepth, VendorMultiplier


class FinishCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinishCode
        fields = ["id", "us_code", "bhma_code", "description", "base_metal",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class ThroatDepthSerializer(serializers.ModelSerializer):
    class Meta:
        model = ThroatDepth
        fields = ["id", "wall_type", "throat_depth_inches", "is_custom", "notes",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class MarginBandSerializer(serializers.ModelSerializer):
    # Derived from target_margin_pct so the two can never disagree.
    divisor = serializers.DecimalField(max_digits=6, decimal_places=4, read_only=True)

    class Meta:
        model = MarginBand
        fields = ["id", "product_type_band", "target_margin_pct", "floor_margin_pct",
                  "divisor", "effective_date", "created_at", "updated_at"]
        read_only_fields = ["id", "divisor", "created_at", "updated_at"]


class VendorMultiplierSerializer(serializers.ModelSerializer):
    #: Derived, never stored — see the model. A stored staleness flag is wrong the
    #: day after it is written, which is precisely when it matters.
    is_stale = serializers.BooleanField(read_only=True)

    class Meta:
        model = VendorMultiplier
        fields = ["id", "vendor_name", "tier", "multiplier", "source_sheet_version",
                  "sheet_name", "protected_until", "steward", "reviewed_on", "note",
                  "is_stale", "effective_date", "created_at", "updated_at"]
        read_only_fields = ["id", "is_stale", "created_at", "updated_at"]


class TaxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRate
        fields = ["id", "jurisdiction", "rate_pct", "description", "effective_date",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
