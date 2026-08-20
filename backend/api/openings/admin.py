from django.contrib import admin

from .models import DocElement, ExtractionRun, FieldProvenance, FieldProvenanceElement, Match, Opening


@admin.register(DocElement)
class DocElementAdmin(admin.ModelAdmin):
    list_display = ("document", "page_number", "element_path", "element_type", "ocr_confidence")
    list_filter = ("element_type", "page_number")
    search_fields = ("element_path", "text")


@admin.register(ExtractionRun)
class ExtractionRunAdmin(admin.ModelAdmin):
    list_display = ("document", "model_id", "prompt_version", "status", "started_at", "completed_at")
    list_filter = ("status", "prompt_version")


@admin.register(Opening)
class OpeningAdmin(admin.ModelAdmin):
    list_display = ("project", "door_number", "handing", "fire_rating_minutes", "review_state")
    list_filter = ("review_state", "handing", "fire_rating_source_location")
    search_fields = ("door_number", "hardware_group", "alternate_designation")


@admin.register(FieldProvenance)
class FieldProvenanceAdmin(admin.ModelAdmin):
    list_display = ("extraction_run", "opening", "field_name", "review_state", "final_confidence")
    list_filter = ("review_state", "field_name")


@admin.register(FieldProvenanceElement)
class FieldProvenanceElementAdmin(admin.ModelAdmin):
    list_display = ("field_provenance", "doc_element", "ordinal")


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        "opening", "catalog_item", "rank", "match_confidence", "status",
        "rating_ok", "handing_ok", "finish_ok",
    )
    list_filter = ("status", "rating_ok", "handing_ok", "division_ok", "is_direct_equal")
