"""
Central reference library endpoints (FR-3).

Explicitly not per-project: this is the fix for the Excel-workbook-per-job status
quo, where hardware sets lived inside whichever job file last used them.
"""

from rest_framework import viewsets

from .models import CatalogItem
from .serializers import CatalogItemSerializer


class CatalogItemViewSet(viewsets.ModelViewSet):
    queryset = CatalogItem.objects.select_related("finish_code").order_by("vendor", "sku")
    serializer_class = CatalogItemSerializer
    filterset_fields = [
        "vendor", "series", "product_type_band", "line_group",
        "csi_division", "is_stock", "is_active", "fire_rating_minutes", "handing",
    ]
    search_fields = ["vendor", "sku", "series", "part_number", "description", "p21_item_id"]
    ordering_fields = ["vendor", "sku", "list_price"]
