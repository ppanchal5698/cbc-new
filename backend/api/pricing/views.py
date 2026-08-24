"""
Reference-data endpoints.

Everything here is effective-dated. Updating a rate creates a **new row with a
later effective date**; it never mutates the old one, because a quote issued in
March must reproduce identically in September (§6.2 step 5).
"""

from datetime import date

from common.permissions import IsAdminOrReadOnly
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import FinishCode, MarginBand, TaxRate, ThroatDepth, VendorMultiplier
from .serializers import (
    FinishCodeSerializer,
    MarginBandSerializer,
    TaxRateSerializer,
    ThroatDepthSerializer,
    VendorMultiplierSerializer,
)

AS_OF = OpenApiParameter(
    "as_of", str, description="ISO date. Returns only rows in force on that date."
)


class EffectiveDatedViewSet(viewsets.ModelViewSet):
    """Adds ``?as_of=YYYY-MM-DD`` to every effective-dated reference table."""

    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        as_of = self.request.query_params.get("as_of")
        if as_of:
            try:
                return qs.as_of(date.fromisoformat(as_of))
            except ValueError:
                pass  # an unparseable date falls through to the full history
        return qs


class FinishCodeViewSet(viewsets.ModelViewSet):
    """
    Dual finish-nomenclature interpreter (NR-3).

    US19 and US26D are separate rows and must stay that way (§1.3).
    """

    permission_classes = [IsAdminOrReadOnly]

    queryset = FinishCode.objects.order_by("us_code")
    serializer_class = FinishCodeSerializer
    filterset_fields = ["us_code", "bhma_code", "base_metal"]
    search_fields = ["us_code", "bhma_code", "description"]


class ThroatDepthViewSet(viewsets.ModelViewSet):

    permission_classes = [IsAdminOrReadOnly]
    queryset = ThroatDepth.objects.order_by("throat_depth_inches")
    serializer_class = ThroatDepthSerializer
    filterset_fields = ["is_custom"]


@extend_schema_view(list=extend_schema(parameters=[AS_OF]))
class MarginBandViewSet(EffectiveDatedViewSet):
    queryset = MarginBand.objects.order_by("product_type_band", "-effective_date")
    serializer_class = MarginBandSerializer
    filterset_fields = ["product_type_band"]


@extend_schema_view(list=extend_schema(parameters=[AS_OF]))
class VendorMultiplierViewSet(EffectiveDatedViewSet):
    queryset = VendorMultiplier.objects.order_by("vendor_name", "-effective_date")
    serializer_class = VendorMultiplierSerializer
    filterset_fields = ["vendor_name", "tier"]

    @extend_schema(
        summary="Record that this sheet was checked today",
        request=None,
        responses={200: VendorMultiplierSerializer},
        description=(
            "Stamps `reviewed_on`. It records that a person looked; it does **not** "
            "fetch a new sheet or change a multiplier.\n\n"
            "That distinction is the whole point. No automatic refresh exists "
            "anywhere in the pricing path, because a price that moves underneath an "
            "estimator without their knowledge is precisely the stale-data failure "
            "NFR-10 is about. Changing a multiplier means a new effective-dated row, "
            "so a quote issued in March still reproduces in September."
        ),
    )
    @action(detail=True, methods=["post"], url_path="mark-reviewed")
    def mark_reviewed(self, request, pk=None):
        book = self.get_object()
        book.reviewed_on = date.today()
        book.save(update_fields=["reviewed_on", "updated_at"])
        return Response(VendorMultiplierSerializer(book).data)


@extend_schema_view(list=extend_schema(parameters=[AS_OF]))
class TaxRateViewSet(EffectiveDatedViewSet):
    """Only OH and KY are taxable; every other jurisdiction is untaxed by rule (§1.1)."""

    queryset = TaxRate.objects.order_by("jurisdiction", "-effective_date")
    serializer_class = TaxRateSerializer
    filterset_fields = ["jurisdiction"]
