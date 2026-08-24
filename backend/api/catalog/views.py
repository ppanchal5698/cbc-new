"""
Central reference library endpoints (FR-3).

Explicitly not per-project: this is the fix for the Excel-workbook-per-job status
quo, where hardware sets lived inside whichever job file last used them.
"""

from common.permissions import IsAdminOrReadOnly
from rest_framework import viewsets

from .models import CatalogItem
from .serializers import CatalogItemSerializer


class CatalogItemViewSet(viewsets.ModelViewSet):

    permission_classes = [IsAdminOrReadOnly]
    queryset = (
        CatalogItem.objects.select_related("finish_code")
        .prefetch_related("cross_references")
        .order_by("vendor", "sku")
    )
    serializer_class = CatalogItemSerializer
    filterset_fields = [
        "vendor", "series", "product_type_band", "line_group",
        "csi_division", "is_stock", "is_active", "fire_rating_minutes", "handing",
    ]
    # Cross-references are searched too, which is the point of holding them: a
    # specification names a Bobrick part and CBC quotes the ASI equivalent, so
    # typing the Bobrick number has to find the item CBC actually sells (§1.4).
    search_fields = [
        "vendor", "sku", "series", "part_number", "description", "p21_item_id",
        "cross_references__part_number", "cross_references__brand",
    ]
    ordering_fields = ["vendor", "sku", "list_price"]
