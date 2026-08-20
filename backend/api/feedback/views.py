"""
Feedback endpoints (FR-13, §5.10).

Rows are normally written as a side effect of a review-UI edit rather than posted
directly; the writable endpoint exists so a correction made outside the standard
flow still lands in the tuning dataset.
"""

from rest_framework import viewsets

from .models import ExtractionMetric, Feedback
from .serializers import ExtractionMetricSerializer, FeedbackSerializer


class FeedbackViewSet(viewsets.ModelViewSet):
    queryset = Feedback.objects.select_related("changed_by").all()
    serializer_class = FeedbackSerializer
    filterset_fields = [
        "entity_type", "entity_id", "field_name", "extraction_run", "used_for_training",
    ]
    ordering_fields = ["changed_at"]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(changed_by=user)


class ExtractionMetricViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Per-run quality counters.

    Citation-rejection rate is the earliest warning that a prompt change or a model
    version bump has degraded quality (§5.6).
    """

    queryset = ExtractionMetric.objects.select_related("extraction_run").all()
    serializer_class = ExtractionMetricSerializer
    filterset_fields = ["extraction_run"]
