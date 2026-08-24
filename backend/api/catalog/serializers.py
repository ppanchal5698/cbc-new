"""Serializers for the central reference library (FR-3)."""

from rest_framework import serializers

from .models import CatalogItem, CatalogItemXref


class CatalogItemXrefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogItemXref
        fields = ["id", "catalog_item", "brand", "part_number"]
        read_only_fields = ["id"]


class CatalogItemSerializer(serializers.ModelSerializer):
    finish_us_code = serializers.CharField(source="finish_code.us_code", read_only=True, default=None)
    finish_bhma_code = serializers.CharField(source="finish_code.bhma_code", read_only=True, default=None)
    cross_references = CatalogItemXrefSerializer(many=True, read_only=True)

    class Meta:
        model = CatalogItem
        fields = [
            "id", "vendor", "series", "sku", "part_number", "description",
            "list_price", "list_price_effective_date", "list_price_sheet_version",
            "product_type_band", "line_group", "csi_division",
            "finish_code", "finish_us_code", "finish_bhma_code",
            "fire_rating_minutes", "handing", "is_stock", "is_active",
            "p21_item_id", "cross_references", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
