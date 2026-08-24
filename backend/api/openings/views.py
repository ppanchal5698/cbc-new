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

from shared.enums import FeedbackEntity, ItemSource, ReviewState

from .models import (
    DocElement,
    ExtractionRun,
    FieldProvenance,
    HardwareSetComponent,
    Match,
    Opening,
)
from .serializers import (
    DocElementSerializer,
    ExtractionRunSerializer,
    FieldProvenanceDetailSerializer,
    FieldProvenanceGridSerializer,
    FieldProvenanceOverrideSerializer,
    HardwareSetComponentSerializer,
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


class OpeningViewSet(viewsets.ModelViewSet):
    """
    The line-item ledger (FR-2, FR-8, FR-9).

    Writable, unlike the read-only grid it replaces. An estimator triaging a bid
    set corrects marks, quantities and descriptions in place, adds the items the
    drawings never carried, and drops the ones that were read twice — and §1.6
    phase 5 is explicit that this judgment is the job rather than an exception to
    it.

    **Every mutation writes a feedback row** (FR-13). That table is simultaneously
    the audit trail and the tuning dataset, and an edit that skipped it would be a
    correction the extraction never learns from.

    ``prefetch_related("provenance")`` plus the grid serializer keeps reads to two
    queries regardless of item count. The citation join is deliberately not
    traversed here (bottleneck B12).
    """

    queryset = Opening.objects.select_related(
        "finish_code", "throat_depth", "duplicate_of"
    ).order_by("sheet_label", "cell_label", "door_number")
    serializer_class = OpeningSerializer
    filterset_fields = [
        "project", "extraction_run", "review_state", "source_kind", "handing",
        "csi_division", "fire_rating_minutes", "fire_rating_absent", "handing_absent",
        "hardware_group", "bid_alternate",
    ]
    search_fields = [
        "door_number", "description", "hardware_group", "alternate_designation",
        "csi_division", "sheet_label",
    ]

    #: The six the prototype's edit grid exposes. A change to any of them is an
    #: estimator correcting what was read, which is the signal §5.10 wants most.
    TRACKED = (
        "door_number", "description", "size_raw", "quantity",
        "csi_division", "hardware_group",
    )

    def get_queryset(self):
        return super().get_queryset().prefetch_related("provenance")

    # -- FR-13: one feedback row per changed field, never two -----------------

    def _log(self, opening, field, before, after, reason=""):
        from feedback.models import Feedback

        Feedback.objects.create(
            entity_type=FeedbackEntity.OPENING.value,
            entity_id=opening.id,
            field_name=field,
            value_before="" if before is None else str(before),
            value_after="" if after is None else str(after),
            extraction_run=opening.extraction_run,
            changed_by=self.request.user if self.request.user.is_authenticated else None,
            reason=reason,
        )

    def perform_create(self, serializer):
        """
        An item the drawings did not carry.

        Marked MANUAL rather than EXTRACTED: it has no citation and never will,
        and showing it beside extracted rows without that distinction would imply
        a document said something no document said.
        """
        opening = serializer.save(
            source_kind=ItemSource.MANUAL.value,
            sheet_label="Added by hand",
            review_state=ReviewState.CORRECTED.value,
        )
        self._log(
            opening, "__created__", None,
            opening.description or opening.door_number, reason="Added by hand",
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {f: getattr(instance, f) for f in self.TRACKED}
        with transaction.atomic():
            opening = serializer.save()
            for field, old in before.items():
                new = getattr(opening, field)
                if str(old) != str(new):
                    self._log(opening, field, old, new)

    def perform_destroy(self, instance):
        with transaction.atomic():
            self._log(
                instance, "__deleted__",
                instance.description or instance.door_number, None,
            )
            instance.delete()

    # -- the ledger's own actions ---------------------------------------------

    def _confirm(self, opening):
        before = opening.source_kind
        opening.source_kind = ItemSource.EXTRACTED.value
        opening.review_state = ReviewState.CONFIRMED.value
        opening.save(update_fields=["source_kind", "review_state", "updated_at"])
        self._log(opening, "source_kind", before, opening.source_kind, reason="Confirmed")
        return opening

    @extend_schema(
        summary="Confirm one item (FR-9)",
        request=None,
        responses={200: OpeningSerializer},
        description="Marks it read cleanly. The estimator has looked and is content.",
    )
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return Response(OpeningSerializer(self._confirm(self.get_object())).data)

    @extend_schema(
        summary="Confirm every item on a bid still needing a look",
        request=None,
        responses={200: OpeningSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="confirm-all")
    def confirm_all(self, request):
        project = request.data.get("project") or request.query_params.get("project")
        if not project:
            return Response(
                {"detail": "project is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        queryset = self.get_queryset().filter(
            project=project, source_kind=ItemSource.REVIEW.value
        )
        with transaction.atomic():
            confirmed = [self._confirm(o) for o in queryset]
        return Response(OpeningSerializer(confirmed, many=True).data)

    @extend_schema(
        summary="Confirm a picked set of items",
        request=None,
        responses={200: OpeningSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="bulk-confirm")
    def bulk_confirm(self, request):
        ids = request.data.get("ids") or []
        with transaction.atomic():
            confirmed = [self._confirm(o) for o in self.get_queryset().filter(id__in=ids)]
        return Response(OpeningSerializer(confirmed, many=True).data)

    @extend_schema(
        summary="Remove a picked set of items", request=None, responses={204: None}
    )
    @action(detail=False, methods=["post"], url_path="bulk-remove")
    def bulk_remove(self, request):
        ids = request.data.get("ids") or []
        with transaction.atomic():
            for opening in self.get_queryset().filter(id__in=ids):
                self._log(
                    opening, "__deleted__",
                    opening.description or opening.door_number, None,
                )
                opening.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _twin(self, opening):
        return opening.duplicate_of or opening.duplicates.first()

    @extend_schema(
        summary="Resolve a duplicate by keeping this reading",
        request=None,
        responses={200: OpeningSerializer},
        description=(
            "Drops the other reading of the same physical item and clears the flag "
            "on this one. The estimator has decided which document to price from."
        ),
    )
    @action(detail=True, methods=["post"], url_path="keep-one")
    def keep_one(self, request, pk=None):
        opening = self.get_object()
        twin = self._twin(opening)
        with transaction.atomic():
            if twin is not None:
                self._log(
                    opening, "__duplicate_resolved__", twin.id, opening.id,
                    reason="Kept this reading; the other was dropped",
                )
                twin.delete()
            opening.duplicate_of = None
            opening.duplicate_note = ""
            opening.source_kind = ItemSource.EXTRACTED.value
            opening.review_state = ReviewState.CONFIRMED.value
            opening.save()
        return Response(OpeningSerializer(opening).data)

    @extend_schema(
        summary="Resolve a duplicate by keeping both readings",
        request=None,
        responses={200: OpeningSerializer(many=True)},
        description="They looked alike but are two real items. Both get priced.",
    )
    @action(detail=True, methods=["post"], url_path="keep-both")
    def keep_both(self, request, pk=None):
        opening = self.get_object()
        kept = []
        with transaction.atomic():
            for row in filter(None, (opening, self._twin(opening))):
                row.duplicate_of = None
                row.duplicate_note = ""
                row.source_kind = ItemSource.EXTRACTED.value
                row.review_state = ReviewState.CONFIRMED.value
                row.save()
                kept.append(row)
            self._log(
                opening, "__duplicate_resolved__", "duplicate", "kept both",
                reason="Two separate items",
            )
        return Response(OpeningSerializer(kept, many=True).data)

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


class HardwareSetComponentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Resolved hardware-set components (§5.11).

    Read-only: a component is what the Division 08 spec section says, and editing
    it here would break its provenance chain. Correcting one is an edit to the
    *quote line* it produced, which is where FR-9 puts every other estimator
    correction and where the FR-13 feedback row is written.

    Filter by ``resolved=false`` to see the callouts the system refused to invent.
    """

    queryset = HardwareSetComponent.objects.select_related("project", "extraction_run").all()
    serializer_class = HardwareSetComponentSerializer
    filterset_fields = ["project", "extraction_run", "hardware_group", "resolved", "review_state"]
    search_fields = ["hardware_group", "description", "manufacturer", "part_number"]
    ordering_fields = ["hardware_group", "component_index", "created_at"]
