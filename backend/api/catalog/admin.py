from django.contrib import admin

from .models import CatalogItem


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = (
        "vendor", "sku", "series", "part_number", "product_type_band",
        "csi_division", "is_stock", "is_active",
    )
    list_filter = ("vendor", "product_type_band", "line_group", "csi_division", "is_stock", "is_active")
    search_fields = ("vendor", "sku", "series", "part_number", "description", "p21_item_id")
