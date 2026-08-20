from django.contrib import admin

from .models import ExtractionMetric, Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("entity_type", "field_name", "value_before", "value_after", "changed_by", "changed_at")
    list_filter = ("entity_type", "field_name", "used_for_training", "changed_at")
    search_fields = ("field_name", "value_before", "value_after")
    readonly_fields = ("changed_at",)


@admin.register(ExtractionMetric)
class ExtractionMetricAdmin(admin.ModelAdmin):
    """Citation-rejection rate is the earliest warning that a prompt has drifted (§5.6)."""

    list_display = (
        "extraction_run", "fields_emitted", "fields_accepted",
        "fields_rejected_citation", "fields_rejected_grounding",
    )
