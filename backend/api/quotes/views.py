"""
Quote review, approval, and export (FR-7, FR-9, FR-10, FR-11, FR-16, NFR-1).

The governing constraint, quoted from the requirements workbook and binding on
every endpoint here:

    The estimator stays in control of every quote. The copilot drafts, sources,
    and calculates — it does not send.

Concretely: :meth:`QuoteViewSet.approve` is the only path to ``APPROVED``, and
:meth:`QuoteViewSet.export` refuses to run on anything else.
"""

import logging

from common.permissions import IsAdminForDestroy
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from shared.enums import FeedbackEntity, QuoteStatus, VendorRFQStatus

from .models import Quote, QuoteLine, VendorRFQ
from .serializers import (
    QuoteApprovalSerializer,
    QuoteLineSerializer,
    QuoteLineWriteSerializer,
    QuoteSerializer,
    QuoteWriteSerializer,
    VendorRFQSerializer,
)

log = logging.getLogger("cbc.api.quotes")


class QuoteViewSet(viewsets.ModelViewSet):

    permission_classes = [IsAdminForDestroy]
    queryset = Quote.objects.select_related("project", "created_by").order_by("-created_at")
    filterset_fields = ["project", "status", "tax_jurisdiction"]
    ordering_fields = ["created_at", "grand_total"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return QuoteWriteSerializer
        return QuoteSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action in ("retrieve", "list"):
            return qs.prefetch_related("lines__catalog_item")
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user if self.request.user.is_authenticated else None)

    def perform_destroy(self, instance):
        if instance.status != QuoteStatus.DRAFT.value:
            # An approved or exported quote is a record of what was sent, not a
            # working document.
            from rest_framework.exceptions import ValidationError

            raise ValidationError("only DRAFT quotes can be deleted")
        instance.delete()

    @extend_schema(
        summary="Build this quote's lines from the project's matched openings (FR-7)",
        request=None,
        responses={200: QuoteSerializer},
        parameters=[OpenApiParameter("replace", bool, description="Rebuild generated lines.")],
        description=(
            "One line per opening, each door's hardware set immediately beneath it, a "
            "restroom-accessories block, and a freight line that renders TBD. The "
            "pipeline already does this when matching completes; this endpoint is the "
            "regenerate action for after an estimator changes which matches are "
            "accepted. Refuses to overwrite existing generated lines unless "
            "replace=true, and never touches hand-added ones."
        ),
    )
    @action(detail=True, methods=["post"], url_path="generate-lines")
    def generate_lines(self, request, pk=None):
        from .draft_ops import DraftError, generate_lines

        quote = self.get_object()
        replace = str(request.query_params.get("replace", "")).lower() in ("1", "true", "yes")
        try:
            generate_lines(quote, replace=replace)
        except DraftError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(QuoteSerializer(quote).data)

    @extend_schema(
        summary="Recalculate the quote from its lines (§6.2 step 4)",
        request=None,
        responses={200: QuoteSerializer},
        description=(
            "Re-runs assembly: group subtotals, the freight line, and OH/KY-only tax. "
            "Totals are then PERSISTED. Costs and margins already on the lines are "
            "left alone — there is no silent re-sourcing of a price (NFR-10)."
        ),
    )
    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        from .pricing_ops import assemble_quote

        quote = self.get_object()
        if quote.status != QuoteStatus.DRAFT.value:
            return Response(
                {"detail": f"quote is {quote.status}; only DRAFT quotes can be recalculated"},
                status=status.HTTP_409_CONFLICT,
            )
        assemble_quote(quote)
        return Response(QuoteSerializer(quote).data)

    @extend_schema(
        summary="Approve a quote (NFR-1 hard gate)",
        request=QuoteApprovalSerializer,
        responses={200: QuoteSerializer},
        description=(
            "The only transition to APPROVED. No send or export path exists without "
            "it. Refuses while any line is still flagged for review."
        ),
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        payload = QuoteApprovalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if not payload.validated_data["confirm"]:
            return Response(
                {"detail": "confirm must be true"}, status=status.HTTP_400_BAD_REQUEST
            )

        quote = self.get_object()
        if quote.status != QuoteStatus.DRAFT.value:
            return Response(
                {"detail": f"quote is already {quote.status}"}, status=status.HTTP_409_CONFLICT
            )

        # NFR-2: unmatched or low-confidence items are never silently accepted.
        # Approval is the last moment anyone looks, so it is where this is enforced.
        unresolved = quote.lines.filter(needs_review=True).count()
        if unresolved:
            return Response(
                {
                    "detail": (
                        f"{unresolved} line(s) still flagged for review. Resolve them "
                        f"before approving — nothing is sent without explicit approval "
                        f"of every line."
                    ),
                    "lines_needing_review": unresolved,
                },
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            quote.status = QuoteStatus.APPROVED.value
            quote.approved_by = request.user if request.user.is_authenticated else None
            quote.approved_at = timezone.now()
            if payload.validated_data.get("notes"):
                quote.notes = f"{quote.notes}\n{payload.validated_data['notes']}".strip()
            quote.save(update_fields=["status", "approved_by", "approved_at", "notes", "updated_at"])

        log.info("quote approved", extra={"quote_id": str(quote.id), "user": str(quote.approved_by)})
        return Response(QuoteSerializer(quote).data)

    @extend_schema(
        summary="Render the customer-facing PDF (FR-10)",
        request=None,
        responses={202: QuoteSerializer},
        description=(
            "Enqueues the WeasyPrint render rather than blocking a request thread "
            "(bottleneck B14) and routes the result to the captured initiator, never "
            "a group inbox. Requires an APPROVED quote."
        ),
    )
    @action(detail=True, methods=["post"])
    def export(self, request, pk=None):
        from .export_ops import enqueue_quote_export

        quote = self.get_object()
        if quote.status not in (QuoteStatus.APPROVED.value, QuoteStatus.EXPORTED.value):
            return Response(
                {
                    "detail": (
                        f"quote is {quote.status}. A quote must be APPROVED before it "
                        f"can be exported — there is no send path without an explicit "
                        f"approval (NFR-1)."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        job_id = enqueue_quote_export(quote, requested_by=request.user)
        return Response(
            {**QuoteSerializer(quote).data, "export_job_id": job_id},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        summary="Find the closest prior quote (FR-11)",
        parameters=[
            OpenApiParameter("brand", str),
            OpenApiParameter("architect", str),
            OpenApiParameter("gc", str),
        ],
        responses={200: QuoteSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def search(self, request):
        queryset = self.get_queryset()
        for param, field in (
            ("brand", "project__brand__icontains"),
            ("architect", "project__architect__icontains"),
            ("gc", "project__general_contractor__icontains"),
        ):
            value = request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})

        page = self.paginate_queryset(queryset)
        serializer = QuoteSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )


class QuoteLineViewSet(viewsets.ModelViewSet):
    """
    Line editing (FR-9): accept, edit, delete, add.

    Every mutation writes a ``feedback`` row. That is the FR-13 tuning dataset and
    it is also how the system learns which matches estimators actually reject.
    """

    queryset = QuoteLine.objects.select_related("quote", "catalog_item", "opening").all()
    filterset_fields = ["quote", "line_group", "cost_source", "needs_review", "below_floor_flag"]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return QuoteLineWriteSerializer
        return QuoteLineSerializer

    def _assert_draft(self, quote):
        from rest_framework.exceptions import ValidationError

        if quote.status != QuoteStatus.DRAFT.value:
            raise ValidationError(
                f"quote is {quote.status}; lines can only be changed while it is DRAFT"
            )

    def perform_create(self, serializer):
        from .pricing_ops import price_line

        self._assert_draft(serializer.validated_data["quote"])
        line = serializer.save()
        price_line(line)

    def perform_update(self, serializer):
        from feedback.models import Feedback

        from .pricing_ops import price_line

        instance = serializer.instance
        self._assert_draft(instance.quote)
        before = {
            field: getattr(instance, field)
            for field in ("quantity", "our_cost", "margin_pct", "cost_source", "description")
        }

        with transaction.atomic():
            line = serializer.save()
            price_line(line)
            user = self.request.user if self.request.user.is_authenticated else None
            for field, old in before.items():
                new = getattr(line, field)
                if str(old) != str(new):
                    Feedback.objects.create(
                        entity_type=FeedbackEntity.QUOTE_LINE.value,
                        entity_id=line.id,
                        field_name=field,
                        value_before=str(old),
                        value_after=str(new),
                        changed_by=user,
                        reason=line.margin_override_reason if field == "margin_pct" else "",
                    )

    def perform_destroy(self, instance):
        from feedback.models import Feedback

        self._assert_draft(instance.quote)
        with transaction.atomic():
            Feedback.objects.create(
                entity_type=FeedbackEntity.QUOTE_LINE.value,
                entity_id=instance.id,
                field_name="__deleted__",
                value_before=instance.description,
                value_after=None,
                changed_by=self.request.user if self.request.user.is_authenticated else None,
            )
            instance.delete()


class VendorRFQViewSet(viewsets.ModelViewSet):
    """
    The vendor-RFQ loop (FR-16).

    Mark a line "awaiting vendor quote", capture the returned price, slot it into
    the draft. Manual price entry is a required path, not a fallback (NR-2).
    """

    queryset = VendorRFQ.objects.select_related("quote_line").all()
    serializer_class = VendorRFQSerializer
    filterset_fields = ["quote_line", "vendor", "status"]

    def perform_create(self, serializer):
        serializer.save(
            requested_by=self.request.user if self.request.user.is_authenticated else None
        )

    @extend_schema(
        summary="Record a returned vendor price (FR-16)",
        request=None,
        responses={200: VendorRFQSerializer},
    )
    @action(detail=True, methods=["post"], url_path="record-price")
    def record_price(self, request, pk=None):
        from decimal import Decimal, InvalidOperation

        from .pricing_ops import apply_rfq_price

        rfq = self.get_object()
        try:
            price = Decimal(str(request.data["returned_price"]))
        except (KeyError, InvalidOperation):
            return Response(
                {"detail": "returned_price is required and must be numeric"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            rfq.returned_price = price
            rfq.returned_at = timezone.now()
            rfq.status = VendorRFQStatus.RECEIVED.value
            rfq.save(update_fields=["returned_price", "returned_at", "status", "updated_at"])
            apply_rfq_price(rfq)

        return Response(VendorRFQSerializer(rfq).data)
