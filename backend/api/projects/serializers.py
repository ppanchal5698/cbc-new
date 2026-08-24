"""Serializers for projects, documents, the manifest, and job status."""

from rest_framework import serializers

from shared.enums import OCRRoute

from .models import BidAlternate, Document, DocumentManifest, PageDiff, PipelineJob, Project
from .storage_ops import public_raster_url


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id", "project", "filename", "role", "status", "status_detail",
            "page_count", "manifest_complete", "version", "is_encrypted",
            "was_repaired", "split_part_count", "estimated_ocr_cost_usd",
            "size_bytes", "created_at", "updated_at",
        ]
        # The estimator sets none of these: they are outcomes of the verified
        # intake path and the pipeline, and a writable file_key would let a client
        # point a Document at an arbitrary S3 object.
        read_only_fields = fields


class DocumentUploadSerializer(serializers.Serializer):
    """Multipart upload payload for ``POST /api/projects/{id}/documents/``."""

    file = serializers.FileField(help_text="The bid-set PDF. Verified by magic bytes, not extension.")
    role = serializers.CharField(required=False, default="BID_SET")
    ready_for_processing = serializers.BooleanField(
        required=False,
        default=True,
        help_text=(
            "Set false to stage a document without starting the pipeline — useful "
            "when several PDFs of one bid set are uploaded in sequence."
        ),
    )


class ProjectSerializer(serializers.ModelSerializer):
    """
    A bid, as the board and the job record show it.

    The four board columns below are **annotations, not columns** — see
    :mod:`projects.board` for why status is derived from the pipeline rather than
    stored beside it. They are absent unless the view annotated them, which is why
    each declares a default.
    """

    documents = DocumentSerializer(many=True, read_only=True)
    document_count = serializers.IntegerField(read_only=True, required=False)

    board_status = serializers.CharField(read_only=True, required=False)
    quoted_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, required=False
    )
    flag_count = serializers.IntegerField(read_only=True, required=False)
    version_label = serializers.CharField(read_only=True, required=False)
    estimator_initials = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "name", "source_channel", "initiator_email", "initiator_user",
            "rfp_body_text", "brand", "architect", "general_contractor",
            "due_date", "outcome",
            "board_status", "quoted_value", "flag_count", "version_label",
            "estimator_initials",
            "documents", "document_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_estimator_initials(self, obj) -> str:
        """The board's Est. column. Falls back to the initiator's email."""
        user = obj.initiator_user
        source = (user.full_name if user and user.full_name else None) or (
            user.email if user else obj.initiator_email
        )
        parts = [p for p in (source or "").replace("@", " ").split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return (parts[0][:2].upper() if parts else "--")


class ProjectWriteSerializer(serializers.ModelSerializer):
    """
    Create/update payload.

    Kept separate from the read serializer so the nested ``documents`` list does
    not appear as a writable field — a client must go through the verified upload
    endpoint, never attach a document by POSTing a project.
    """

    class Meta:
        model = Project
        # id and created_at are read-only members rather than omitted: a POST
        # response without the created resource's id gives the client nothing to
        # act on, and every caller immediately needs it to upload documents.
        fields = [
            "id", "name", "source_channel", "initiator_email", "initiator_user",
            "rfp_body_text", "brand", "architect", "general_contractor",
            "due_date", "outcome", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class DocumentManifestSerializer(serializers.ModelSerializer):
    """
    One page's preprocessing decision.

    ``skipped`` and ``skip_reason`` are surfaced explicitly because §4.3's design
    rule is *never silently skip*: a page the system decided not to read is exactly
    the omission NFR-2 forbids, and Risk R12 makes the estimator's ability to say
    "read page 47 anyway" a required feature.
    """

    skipped = serializers.SerializerMethodField()
    skip_reason = serializers.SerializerMethodField()
    raster_url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = DocumentManifest
        fields = [
            "id", "document", "page_number", "page_class", "class_confidence",
            "class_method", "text_layer", "native_word_count", "vector_path_count",
            "ocr_route", "route_reason", "skipped", "skip_reason",
            "rotation", "width_pt", "height_pt",
            "raster_url", "thumb_url", "ocr_cost_estimate", "page_hash",
            "forced_by_user",
        ]
        read_only_fields = fields

    def get_skipped(self, obj) -> bool:
        return obj.ocr_route == OCRRoute.SKIP.value

    def get_skip_reason(self, obj) -> str | None:
        if obj.ocr_route != OCRRoute.SKIP.value:
            return None
        return obj.route_reason or f"classified {obj.page_class}; not routed to OCR"

    def get_raster_url(self, obj) -> str | None:
        return public_raster_url(obj.raster_key) if obj.raster_key else None

    def get_thumb_url(self, obj) -> str | None:
        return public_raster_url(obj.thumb_key) if obj.thumb_key else None


class PipelineJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineJob
        fields = [
            "id", "document", "stage", "status", "attempt", "external_job_id",
            "error_detail", "cost_estimate", "cost_actual",
            "started_at", "completed_at", "created_at", "updated_at",
        ]
        read_only_fields = fields


class BidAlternateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BidAlternate
        fields = ["id", "project", "designation", "description", "source_document", "is_base_bid"]


class PageDiffSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageDiff
        fields = ["id", "document", "compared_to_document", "page_number", "status", "notes"]
        read_only_fields = fields
