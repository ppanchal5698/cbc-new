from django.contrib import admin

from .models import FinishCode, MarginBand, TaxRate, ThroatDepth, VendorMultiplier


@admin.register(FinishCode)
class FinishCodeAdmin(admin.ModelAdmin):
    """US19 and US26D must never collapse to the same row (§1.3)."""

    list_display = ("us_code", "bhma_code", "base_metal", "description")
    search_fields = ("us_code", "bhma_code", "description")


@admin.register(ThroatDepth)
class ThroatDepthAdmin(admin.ModelAdmin):
    list_display = ("wall_type", "throat_depth_inches", "is_custom")
    list_filter = ("is_custom",)


@admin.register(MarginBand)
class MarginBandAdmin(admin.ModelAdmin):
    list_display = ("product_type_band", "target_margin_pct", "floor_margin_pct", "effective_date")
    list_filter = ("product_type_band",)


@admin.register(VendorMultiplier)
class VendorMultiplierAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "tier", "multiplier", "source_sheet_version", "effective_date")
    list_filter = ("vendor_name", "tier")


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    """Only OH and KY are taxable (§1.1)."""

    list_display = ("jurisdiction", "rate_pct", "effective_date", "description")
    list_filter = ("jurisdiction",)
