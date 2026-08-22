"""
Project, document, manifest, and job-status endpoints.

The upload action here is the verified intake path (§3.3 steps 1-2). Its
guarantees — magic bytes, checksum, SSE, version-ID, write-once, idempotent
completion — live in :mod:`projects.storage_ops`; this module owns the HTTP
contract and the database transaction around it.
"""

import logging

from common.permissions import IsAdminForDestroy
from django.db import transaction
from django.db.models import Count
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from shared.enums import DocumentStatus, FeedbackEntity, OCRRoute, PipelineStage
from shared.s3_keys import get_source_document_key

from .board import BOARD_FILTERS, annotate_board, board_totals
from .models import BidAlternate, Document, DocumentManifest, PageDiff, PipelineJob, Project
from .serializers import (
    BidAlternateSerializer,
    DocumentManifestSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    PageDiffSerializer,
    PipelineJobSerializer,
    ProjectSerializer,
    ProjectWriteSerializer,
)
from .storage_ops import UploadRejected, put_source_document, verify_pdf_bytes

log = logging.getLogger("cbc.api.projects")


class ProjectViewSet(viewsets.ModelViewSet):
    """Bids. A Project is the bid set; a Document is one PDF within it."""

    permission_classes = [IsAdminForDestroy]

    queryset = Project.objects.all().order_by("-created_at")
    filterset_fields = [
        "source_channel", "brand", "architect", "general_contractor", "outcome",
        "initiator_user",
    ]
    search_fields = ["name", "initiator_email", "brand", "architect", "general_contractor"]
    ordering_fields = ["created_at", "name", "due_date", "quoted_value"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ProjectWriteSerializer
        return ProjectSerializer

    def get_queryset(self):
        qs = annotate_board(super().get_queryset().select_related("initiator_user"))
        if self.action in ("list", "summary"):
            # The list view does not need every nested document, and prefetching
            # them for a hundred projects is the kind of fan-out §9 warns about.
            # The board columns are derived, not stored — see projects/board.py.
            return qs.annotate(document_count=Count("documents"))
        # The detail view carries them too: the job record on Stage 1 shows the
        # same status the board does, and the two disagreeing would be worse than
        # either being absent.
        return qs.prefetch_related("documents")

    def filter_queryset(self, queryset):
        """
        The board's filter chips, on top of the ordinary DRF filters.

        They select on ``board_status``, which is an annotation rather than a
        column, so django-filter cannot express them — but a Q against the
        annotation can, and it stays in one place (:data:`BOARD_FILTERS`).
        """
        queryset = super().filter_queryset(queryset)
        chip = self.request.query_params.get("board_filter")
        if chip == "Mine":
            user = self.request.user
            return queryset.filter(initiator_user=user) if user.is_authenticated else queryset.none()
        if chip and chip in BOARD_FILTERS:
            return queryset.filter(BOARD_FILTERS[chip])
        return queryset

    @extend_schema(
        summary="Bid-board header totals for the current filter",
        parameters=[
            OpenApiParameter(
                "board_filter", str, description="All / Mine / In flight / Sent / Closed"
            )
        ],
        responses={200: dict},
        description=(
            "Counted across every matching bid, not just the page — a header that "
            "silently described one page of a filtered board would be wrong more "
            "often than right."
        ),
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(board_totals(self.filter_queryset(self.get_queryset())))

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        if serializer.validated_data.get("initiator_user") is None:
            serializer.save(initiator_user=user)
        else:
            serializer.save()

    @extend_schema(
        summary="Upload a bid-set PDF (FR-1)",
        request={"multipart/form-data": DocumentUploadSerializer},
        responses={201: DocumentSerializer},
        description=(
            "Verifies magic bytes and checksum, writes once to the Object-Locked "
            "source bucket with SSE, captures the S3 version-ID, and — unless "
            "ready_for_processing is false — moves the document to "
            "READY_FOR_PROCESSING, which enqueues it to SQS."
        ),
    )
    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def documents(self, request, pk=None):
        project = self.get_object()
        payload = DocumentUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        upload = payload.validated_data["file"]
        data = upload.read()

        try:
            verify_pdf_bytes(data, declared_name=upload.name)
        except UploadRejected as exc:
            # A rejected upload never reaches S3. The source bucket is write-once
            # under Object Lock, so a bad object there cannot be cleaned up.
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        version = (
            Document.objects.filter(project=project, filename=upload.name).count() + 1
        )

        with transaction.atomic():
            document = Document.objects.create(
                project=project,
                filename=upload.name,
                role=payload.validated_data["role"],
                # Placeholder: the real key needs the document's own UUID, which
                # only exists after the insert.
                file_key="",
                version=version,
                status=DocumentStatus.UPLOADED.value,
            )
            key = get_source_document_key(str(project.id), str(document.id), version)
            try:
                stored = put_source_document(key, data)
            except UploadRejected as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            document.file_key = stored["key"]
            document.file_version_id = stored["version_id"]
            document.checksum_sha256 = stored["checksum_sha256"]
            document.size_bytes = stored["size_bytes"]
            if payload.validated_data["ready_for_processing"]:
                # The post_save signal enqueues on this transition, inside
                # transaction.on_commit — never before the row is durable.
                document.status = DocumentStatus.READY_FOR_PROCESSING.value
            document.save()

        return Response(DocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class DocumentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Documents are created only through the verified upload path.

    Read-only on purpose: a writable ``file_key`` would let a client point a
    Document row at an arbitrary S3 object and defeat the whole provenance chain.
    """

    queryset = Document.objects.select_related("project").order_by("-created_at")
    serializer_class = DocumentSerializer
    filterset_fields = ["project", "status", "role", "manifest_complete"]
    ordering_fields = ["created_at", "filename"]

    @extend_schema(
        summary="Preprocessing manifest for a document (§4.1)",
        parameters=[
            OpenApiParameter("skipped_only", bool, description="Only pages that were not read."),
        ],
        responses={200: DocumentManifestSerializer(many=True)},
        description=(
            "One row per page with its classification, the tier that resolved it, "
            "its OCR route, and — for every SKIP — the reason. This is the audit "
            "answer to 'why didn't the system read page 47?'"
        ),
    )
    @action(detail=True, methods=["get"])
    def manifest(self, request, pk=None):
        document = self.get_object()
        pages = document.manifest_pages.all()
        if request.query_params.get("skipped_only", "").lower() in ("1", "true", "yes"):
            pages = pages.filter(ocr_route=OCRRoute.SKIP.value)
        page = self.paginate_queryset(pages)
        serializer = DocumentManifestSerializer(page if page is not None else pages, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @extend_schema(
        summary="Pipeline status for a document (§7.7)",
        responses={200: PipelineJobSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="pipeline-jobs")
    def pipeline_jobs(self, request, pk=None):
        jobs = self.get_object().pipeline_jobs.all()
        ordered = sorted(jobs, key=lambda j: PipelineStage(j.stage).index)
        return Response(PipelineJobSerializer(ordered, many=True).data)

    @extend_schema(
        summary="Page diffs against an earlier document (§4.7)",
        responses={200: PageDiffSerializer(many=True)},
        description=(
            "Which pages changed when this addendum arrived. Unchanged pages reuse "
            "their existing elements and extractions at zero cost. The diff is a "
            "report only — no reconciliation logic exists until Open Item 11 is "
            "answered (Risk R2)."
        ),
    )
    @action(detail=True, methods=["get"], url_path="page-diffs")
    def page_diffs(self, request, pk=None):
        diffs = PageDiff.objects.filter(document=self.get_object())
        return Response(PageDiffSerializer(diffs, many=True).data)


class DocumentManifestViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Per-page preprocessing decisions, with the force-read override.

    The override is not a convenience. Triage is the largest cost and latency win
    in the system and it introduces one new failure mode: a schedule the classifier
    did not recognise. Risk R12's mitigation is that every SKIP is visible with a
    reason and the estimator can force any page to be read.
    """

    queryset = DocumentManifest.objects.select_related("document").all()
    serializer_class = DocumentManifestSerializer
    filterset_fields = ["document", "page_class", "ocr_route", "text_layer", "class_method"]

    @extend_schema(
        summary="Force a page to be read (Risk R12)",
        request=None,
        responses={202: DocumentManifestSerializer},
        description=(
            "Overrides the routing decision for one page, re-queues just that page, "
            "and writes a feedback row so the Tier 1-3 anchors improve over time "
            "(FR-13)."
        ),
    )
    @action(detail=True, methods=["post"], url_path="force-read")
    def force_read(self, request, pk=None):
        from feedback.models import Feedback

        page = self.get_object()
        previous_route = page.ocr_route
        requested = request.data.get("ocr_route", OCRRoute.TEXTRACT_TABLES.value)
        if requested not in OCRRoute.values():
            return Response(
                {"detail": f"ocr_route must be one of {OCRRoute.values()}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            page.ocr_route = requested
            page.route_reason = "forced by estimator"
            page.forced_by_user = request.user if request.user.is_authenticated else None
            page.save(update_fields=["ocr_route", "route_reason", "forced_by_user", "updated_at"])

            # Every forced read is training data: it says the classifier was wrong
            # about a page, which is exactly what Tier 1-3 anchors need (§4.3).
            Feedback.objects.create(
                entity_type=FeedbackEntity.DOCUMENT_MANIFEST.value,
                entity_id=page.id,
                field_name="ocr_route",
                value_before=previous_route,
                value_after=requested,
                changed_by=request.user if request.user.is_authenticated else None,
                reason=request.data.get("reason", ""),
            )

        return Response(
            DocumentManifestSerializer(page).data, status=status.HTTP_202_ACCEPTED
        )


class PipelineJobViewSet(viewsets.ReadOnlyModelViewSet):
    """Status polling. Reads the same table the worker writes (§3.2 rule 2)."""

    queryset = PipelineJob.objects.select_related("document").order_by("-created_at")
    serializer_class = PipelineJobSerializer
    filterset_fields = ["document", "project", "stage", "status"]


class BidAlternateViewSet(viewsets.ModelViewSet):
    """
    FR-14 data model, flag-gated (§7.6).

    The schema exists so base-bid and alternate totals can present as separate
    comparable figures. **No reconciliation logic** — CBC has not said whether an
    addendum is a new document, a revision, or both (Open Item 11, Risk R2).
    """

    queryset = BidAlternate.objects.select_related("project").all()
    serializer_class = BidAlternateSerializer
    filterset_fields = ["project", "is_base_bid"]
