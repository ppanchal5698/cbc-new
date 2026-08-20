from django.contrib import admin

from .models import Quote, QuoteLine, VendorRFQ


class QuoteLineInline(admin.TabularInline):
    model = QuoteLine
    extra = 0
    fields = ("line_group", "description", "quantity", "our_cost", "margin_pct", "sale_each", "extended", "below_floor_flag")
    readonly_fields = ("sale_each", "extended")


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "grand_total", "tax_jurisdiction", "approved_by", "approved_at")
    list_filter = ("status", "tax_jurisdiction")
    inlines = [QuoteLineInline]
    readonly_fields = ("approved_at", "exported_at", "export_key")


@admin.register(QuoteLine)
class QuoteLineAdmin(admin.ModelAdmin):
    list_display = ("quote", "line_group", "description", "quantity", "our_cost", "margin_pct", "sale_each", "extended", "cost_is_stale", "below_floor_flag")
    list_filter = ("line_group", "cost_source", "cost_is_stale", "below_floor_flag", "margin_overridden")


@admin.register(VendorRFQ)
class VendorRFQAdmin(admin.ModelAdmin):
    list_display = ("quote_line", "vendor", "status", "requested_at", "returned_price", "returned_at")
    list_filter = ("status", "vendor")
