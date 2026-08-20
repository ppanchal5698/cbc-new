from django.contrib import admin

from .models import BidAlternate, Document, DocumentManifest, PageDiff, PipelineJob, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "source_channel", "initiator_email", "brand", "general_contractor", "created_at")
    list_filter = ("source_channel", "brand")
    search_fields = ("name", "initiator_email", "brand", "architect", "general_contractor")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("filename", "project", "role", "status", "page_count", "manifest_complete", "version")
    list_filter = ("status", "role", "manifest_complete", "was_repaired", "is_encrypted")
    search_fields = ("filename", "file_key")
    readonly_fields = ("file_key", "file_version_id", "checksum_sha256")


@admin.register(DocumentManifest)
class DocumentManifestAdmin(admin.ModelAdmin):
    """Every SKIP must be visible with its reason (§4.3 design rule, Risk R12)."""

    list_display = ("document", "page_number", "page_class", "class_method", "text_layer", "ocr_route", "route_reason")
    list_filter = ("page_class", "ocr_route", "text_layer", "class_method")


@admin.register(PipelineJob)
class PipelineJobAdmin(admin.ModelAdmin):
    list_display = ("document", "stage", "status", "attempt", "external_job_id", "cost_actual", "completed_at")
    list_filter = ("stage", "status")
    search_fields = ("external_job_id", "idempotency_key")


@admin.register(BidAlternate)
class BidAlternateAdmin(admin.ModelAdmin):
    list_display = ("project", "designation", "is_base_bid", "source_document")
    list_filter = ("is_base_bid",)


@admin.register(PageDiff)
class PageDiffAdmin(admin.ModelAdmin):
    list_display = ("document", "compared_to_document", "page_number", "status")
    list_filter = ("status",)
