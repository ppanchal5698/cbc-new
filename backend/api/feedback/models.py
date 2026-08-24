"""
Estimator corrections (FR-13, §7.5, §5.10).

A row is written on **every** review-UI edit. This table is simultaneously the
tuning dataset, the source of new golden-set cases, and the empirical answer to
several of CBC's open items — a forced page read answers "where do schedules live",
a corrected rating answers Open Item 9.
"""

import uuid

from django.conf import settings
from django.db import models
from projects.models import TimestampedModel

from shared.enums import FeedbackEntity


class Feedback(models.Model):
    """
    One before/after correction.

    ``entity_type`` + ``entity_id`` rather than a foreign key per target: the same
    correction shape applies to an opening field, a match, a quote line, and a
    forced page read, and five nullable FKs would be five ways to write the same
    row inconsistently.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    entity_type = models.CharField(max_length=50, choices=FeedbackEntity.choices(), db_index=True)
    entity_id = models.UUIDField(db_index=True)

    field_name = models.CharField(max_length=100)
    value_before = models.TextField(null=True, blank=True)
    value_after = models.TextField(null=True, blank=True)

    # Traceability back to the exact configuration that produced the wrong value.
    # Nullable because a forced page read (Risk R12) happens before extraction.
    extraction_run = models.ForeignKey(
        "openings.ExtractionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_records",
    )
    field_provenance = models.ForeignKey(
        "openings.FieldProvenance",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_records",
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="feedback_authored",
    )
    reason = models.TextField(blank=True)

    used_for_training = models.BooleanField(
        default=False, help_text="Promoted into the golden set (§5.10)."
    )

    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["extraction_run", "field_name"]),
            models.Index(fields=["field_name", "changed_at"]),
        ]
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return f"{self.entity_type}.{self.field_name}: {self.value_before!r} -> {self.value_after!r}"


class ExtractionMetric(TimestampedModel):
    """
    Per-run quality counters (§5.6, §11.5).

    Citation-rejection rate is one of the two metrics worth watching most closely:
    a rise means the model or prompt has drifted. Stored per run so a regression is
    attributable to an exact prompt version rather than noticed as a vibe.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    extraction_run = models.OneToOneField(
        "openings.ExtractionRun", on_delete=models.CASCADE, related_name="metrics"
    )

    fields_emitted = models.IntegerField(default=0)
    fields_accepted = models.IntegerField(default=0)
    fields_rejected_citation = models.IntegerField(
        default=0, help_text="Cited an element_id not in the supplied set (§5.6 check 1)."
    )
    fields_rejected_grounding = models.IntegerField(
        default=0, help_text="Value not present in the text it cited (§5.6 check 2)."
    )
    fields_flagged_low_confidence = models.IntegerField(default=0)
    fields_null_with_citation = models.IntegerField(default=0)
    schema_repair_retries = models.IntegerField(
        default=0, help_text="At most one per call, and only for malformed JSON (§5.6)."
    )

    # -- cross-schedule resolution (§5.11) ------------------------------------
    # Its own drift signal. A rising unresolved rate means the Division 08 spec
    # section is no longer being located, not that bid sets stopped using named
    # hardware sets — and the visible symptom is quotes quietly missing most of
    # their lines.
    hardware_callouts = models.IntegerField(
        default=0, help_text="Distinct hardware-group callouts found in the door schedule."
    )
    hardware_sets_resolved = models.IntegerField(default=0)
    hardware_sets_unresolved = models.IntegerField(
        default=0, help_text="Callout present, definition not in the document. Never guessed."
    )
    hardware_components_written = models.IntegerField(default=0)

    @property
    def hardware_resolution_rate(self) -> float:
        """Share of callouts whose definition was found in the document."""
        if not self.hardware_callouts:
            return 0.0
        return self.hardware_sets_resolved / self.hardware_callouts

    @property
    def citation_rejection_rate(self) -> float:
        """Share of emitted fields the validation gate refused."""
        if not self.fields_emitted:
            return 0.0
        rejected = self.fields_rejected_citation + self.fields_rejected_grounding
        return rejected / self.fields_emitted

    def __str__(self) -> str:
        return f"metrics for {self.extraction_run_id}"
