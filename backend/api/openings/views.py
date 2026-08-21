"""
Elements, provenance, openings, and match endpoints.

The source-viewer action here is the whole "trace the word to the plan" feature
and it involves **no inference**: `source_element_ids` join `doc_elements`, which
carry a page number and a 0-1 polygon, which the client overlays on a
pre-rendered raster served from the CDN (§5.5, bottleneck B5).
"""

import logging

from common.pagination import ElementPagination
from django.db import transaction
from drf_spectacular.utils import extend_schema
from projects.models import DocumentManifest
from projects.storage_ops import public_raster_url
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from shared.enums import FeedbackEntity, ReviewState

from .models import DocElement, ExtractionRun, FieldProvenance, Match, Opening
from .serializers import (
    DocElementSerializer,
    ExtractionRunSerializer,
    FieldProvenanceDetailSerializer,
    FieldProvenanceGridSerializer,
    FieldProvenanceOverrideSerializer,
    MatchSerializer,
    OpeningSerializer,
    SourceRegionSerializer,
)

log = logging.getLogger("cbc.api.openings")


class DocElementViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Normalised OCR elements.

    Read-only and always paginated: a bid set produces tens of thousands of rows
    even after triage (Risk R9), and ``ocr_confidence`` in particular must never be
    recomputed or overwritten through an API.
    """

    queryset = DocElement.objects.all().order_by("page_number", "reading_order")
    serializer_class = DocElementSerializer
    pagination_class = ElementPagination
    filterset_fields = ["document", "page_number", "element_type", "table_id"]


class ExtractionRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ExtractionRun.objects.select_related("document").order_by("-started_at")
    serializer_class = ExtractionRunSerializer
    filterset_fields = ["document", "status", "prompt_version", "model_id"]


class OpeningViewSet(viewsets.ReadOnlyModelViewSet):
    """
    The openings grid (FR-2, FR-8).

    ``prefetch_related("provenance")`` plus the grid serializer keeps this to two
    queries regardless of opening count. The citation join is deliberately not
    traversed here (bottleneck B12).
    """

    queryset = Opening.objects.select_related("finish_code", "throat_depth").order_by("door_number")
    serializer_class = OpeningSerializer
    filterset_fields = [
        "project", "extraction_run", "review_state", "handing",
        "fire_rating_minutes", "fire_rating_absent", "handing_absent",
        "hardware_group", "bid_alternate",
    ]
    search_fields = ["door_number", "hardware_group", "alternate_designation"]

    def get_queryset(self):
        return super().get_queryset().prefetch_related("provenance")

    @extend_schema(
        summary="Ranked matches for an opening (FR-4)",
        responses={200: MatchSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def matches(self, request, pk=None):
        matches = self.get_object().matches.select_related("catalog_item").all()
        return Response(MatchSerializer(matches, many=True).data)

    @extend_schema(
        summary="Fields needing estimator attention (FR-8)",
        responses={200: FieldProvenanceGridSerializer(many=True)},
        description=(
            "Every field the validation gate rejected or the threshold flagged, "
            "including fire ratings and handings that fell below their stricter "
            "per-field thresholds (§5.8)."
        ),
    )
    @action(detail=True, methods=["get"], url_path="needs-review")
    def needs_review(self, request, pk=None):
        fields = self.get_object().provenance.filter(
            review_state__in=[ReviewState.FLAGGED.value, ReviewState.REJECTED.value]
        )
        return Response(FieldProvenanceGridSerializer(fields, many=True).data)


class FieldProvenanceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Per-field provenance, with the estimator override.

    Read-only by default. The one mutation is :meth:`override`, which is a
    transaction rather than a field assignment: FR-13 requires a feedback row on
    **every** review-UI edit, and an override that skipped it would silently drop
    a row from the tuning dataset.
    """

    # Ordered, because an unordered queryset behind a paginator is not merely
    # untidy: Postgres is free to return rows in a different order per query, so
    # page 2 can repeat a row from page 1 and omit another entirely.
    queryset = (
        FieldProvenance.objects.select_related("opening", "extraction_run")
        .order_by("opening__door_number", "field_name")
    )
    filterset_fields = ["extraction_run", "opening", "field_name", "review_state"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return FieldProvenanceDetailSerializer
        return FieldProvenanceGridSerializer

    @extend_schema(
        summary="Correct or confirm an extracted field (FR-9, FR-13)",
        request=FieldProvenanceOverrideSerializer,
        responses={200: FieldProvenanceDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def override(self, request, pk=None):
        provenance = self.get_object()
        payload = FieldProvenanceOverrideSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        before = provenance.extracted_value
        after = payload.validated_data["extracted_value"]

        with transaction.atomic():
            from feedback.models import Feedback

            provenance.extracted_value = after
            provenance.review_state = payload.validated_data["review_state"]
            provenance.save(update_fields=["extracted_value", "review_state", "updated_at"])

            # Mirror the corrected value onto the opening so the grid and any
            # downstream pricing read the estimator's answer, not the model's.
            opening = provenance.opening
            if opening is not None and hasattr(opening, provenance.field_name):
                setattr(opening, provenance.field_name, after)
                opening.review_state = ReviewState.CORRECTED.value
                opening.save(update_fields=[provenance.field_name, "review_state", "updated_at"])

            Feedback.objects.create(
                entity_type=FeedbackEntity.FIELD_PROVENANCE.value,
                entity_id=provenance.id,
                field_name=provenance.field_name,
                value_before=before,
                value_after=after,
                extraction_run=provenance.extraction_run,
                field_provenance=provenance,
                changed_by=request.user if request.user.is_authenticated else None,
                reason=payload.validated_data.get("reason", ""),
            )

        return Response(FieldProvenanceDetailSerializer(provenance).data)

    @extend_schema(
        summary="Source region for the highlight overlay (§5.5)",
        responses={200: SourceRegionSerializer},
        description=(
            "The pre-rendered page raster URL plus the cited polygons in 0-1 page "
            "fractions. No server-side cropping and no second inference — the "
            "client positions an absolute SVG over the image."
        ),
    )
    @action(detail=True, methods=["get"], url_path="source")
    def source(self, request, pk=None):
        provenance = self.get_object()
        elements = provenance.cited_elements
        if not elements:
            return Response(
                {"detail": "this field cites no elements; there is nothing to show"},
                status=status.HTTP_404_NOT_FOUND,
            )

        page_number = provenance.page_number or elements[0].page_number
        document_id = elements[0].document_id
        manifest = DocumentManifest.objects.filter(
            document_id=document_id, page_number=page_number
        ).first()

        serializer = DocElementSerializer(elements, many=True)
        payload = {
            "page_number": page_number,
            "raster_url": public_raster_url(manifest.raster_key)
            if manifest and manifest.raster_key
            else None,
            "page_width_pt": manifest.width_pt if manifest else None,
            "page_height_pt": manifest.height_pt if manifest else None,
            # The client must apply this: a rotated sheet whose rotation is ignored
            # overlays the highlight 90 degrees off (§4.5).
            "rotation": manifest.rotation if manifest else 0,
            "polygons": [item["polygon"] for item in serializer.data if item["polygon"]],
            "bbox": {
                "x_min": provenance.bbox_x_min,
                "y_min": provenance.bbox_y_min,
                "x_max": provenance.bbox_x_max,
                "y_max": provenance.bbox_y_max,
            },
        }
        return Response(SourceRegionSerializer(payload).data)


class MatchViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Ranked match candidates.

    Accept and reject are explicit actions rather than a writable ``status`` field
    so that an acceptance always carries the estimator who made it.
    """

    queryset = Match.objects.select_related("catalog_item", "opening").all()
    serializer_class = MatchSerializer
    filterset_fields = ["opening", "status", "is_direct_equal", "catalog_item"]

    @extend_schema(summary="Accept a proposed match (FR-9)", request=None, responses={200: MatchSerializer})
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._set_status(request, "ACCEPTED")

    @extend_schema(summary="Reject a proposed match (FR-9)", request=None, responses={200: MatchSerializer})
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._set_status(request, "REJECTED")

    def _set_status(self, request, new_status: str):
        from feedback.models import Feedback

        match = self.get_object()
        before = match.status
        with transaction.atomic():
            match.status = new_status
            if request.data.get("substitution_note"):
                match.substitution_note = request.data["substitution_note"]
                match.is_direct_equal = True
            match.save(update_fields=["status", "substitution_note", "is_direct_equal", "updated_at"])
            Feedback.objects.create(
                entity_type=FeedbackEntity.MATCH.value,
                entity_id=match.id,
                field_name="status",
                value_before=before,
                value_after=new_status,
                changed_by=request.user if request.user.is_authenticated else None,
                reason=request.data.get("reason", ""),
            )
        return Response(MatchSerializer(match).data)
