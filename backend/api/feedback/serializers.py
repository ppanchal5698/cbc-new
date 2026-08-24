"""Serializers for the FR-13 tuning dataset."""

from rest_framework import serializers

from .models import ExtractionMetric, Feedback


class FeedbackSerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(
        source="changed_by.username", read_only=True, default=None
    )

    class Meta:
        model = Feedback
        fields = [
            "id", "entity_type", "entity_id", "field_name",
            "value_before", "value_after", "extraction_run", "field_provenance",
            "changed_by", "changed_by_username", "reason", "used_for_training", "changed_at",
        ]
        read_only_fields = ["id", "changed_by", "changed_by_username", "changed_at"]


class ExtractionMetricSerializer(serializers.ModelSerializer):
    citation_rejection_rate = serializers.FloatField(read_only=True)
    hardware_resolution_rate = serializers.FloatField(read_only=True)

    class Meta:
        model = ExtractionMetric
        fields = [
            "id", "extraction_run", "fields_emitted", "fields_accepted",
            "fields_rejected_citation", "fields_rejected_grounding",
            "fields_flagged_low_confidence", "fields_null_with_citation",
            "schema_repair_retries", "citation_rejection_rate",
            "hardware_callouts", "hardware_sets_resolved", "hardware_sets_unresolved",
            "hardware_components_written", "hardware_resolution_rate",
            "created_at", "updated_at",
        ]
        read_only_fields = fields
