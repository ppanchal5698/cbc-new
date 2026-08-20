"""
The tuning dataset (FR-13, §5.10, §11.5).

    Every estimator correction writes a ``feedback`` row with before/after values,
    the field, the extraction run, and the user. That table is simultaneously the
    tuning dataset, the source of new golden-set cases, and the empirical answer
    to several of CBC's open items.
"""

import pytest
from factories import ExtractionRunFactory, FeedbackFactory, FieldProvenanceFactory, OpeningFactory
from rest_framework import status

from feedback.models import ExtractionMetric, Feedback
from shared.enums import FeedbackEntity

pytestmark = pytest.mark.django_db


class TestFeedbackShape:
    def test_before_and_after_are_both_recorded(self):
        """A correction with no 'before' cannot teach anything."""
        record = FeedbackFactory(value_before="90 MIN", value_after="45 MIN")
        assert record.value_before == "90 MIN"
        assert record.value_after == "45 MIN"

    def test_entity_type_covers_every_correctable_surface(self):
        """
        One shape for every target rather than five nullable foreign keys, which
        would be five ways to write the same row inconsistently.
        """
        assert set(FeedbackEntity.values()) == {
            "OPENING", "FIELD_PROVENANCE", "QUOTE_LINE", "MATCH", "DOCUMENT_MANIFEST",
        }

    def test_a_correction_traces_back_to_the_exact_configuration(self):
        """NFR-3: attributable to a model version and a prompt version."""
        run = ExtractionRunFactory()
        provenance = FieldProvenanceFactory(
            opening=OpeningFactory(extraction_run=run), extraction_run=run
        )
        record = FeedbackFactory(field_provenance=provenance, extraction_run=run)
        assert record.extraction_run.model_id
        assert record.extraction_run.prompt_version

    def test_deleting_a_run_does_not_destroy_the_training_data(self):
        """
        A correction outlives the run that produced it.

        The feedback IS the dataset; cascading it away with a re-extraction would
        discard the most valuable thing the system collects.
        """
        run = ExtractionRunFactory()
        record = FeedbackFactory(extraction_run=run)
        run.delete()
        record.refresh_from_db()
        assert record.extraction_run_id is None
        assert record.value_after


class TestExtractionMetrics:
    def test_citation_rejection_rate_is_derived(self):
        """
        §11.5: one of the two metrics worth watching most closely.

        A rise means the model or prompt has drifted.
        """
        metric = ExtractionMetric.objects.create(
            extraction_run=ExtractionRunFactory(),
            fields_emitted=100,
            fields_accepted=90,
            fields_rejected_citation=6,
            fields_rejected_grounding=4,
        )
        assert metric.citation_rejection_rate == pytest.approx(0.10)

    def test_rate_is_zero_when_nothing_was_emitted(self):
        metric = ExtractionMetric.objects.create(extraction_run=ExtractionRunFactory())
        assert metric.citation_rejection_rate == 0.0

    def test_the_two_gates_are_counted_separately(self):
        """
        The §5.6 gates fail for different reasons and need different responses: a
        citation failure is a hallucinated id, a grounding failure is a fabricated
        value.
        """
        metric = ExtractionMetric.objects.create(
            extraction_run=ExtractionRunFactory(),
            fields_emitted=10,
            fields_rejected_citation=1,
            fields_rejected_grounding=2,
        )
        assert metric.fields_rejected_citation != metric.fields_rejected_grounding


class TestFeedbackApi:
    def test_list_and_filter(self, auth_client):
        FeedbackFactory(field_name="fire_rating")
        FeedbackFactory(field_name="handing")
        assert auth_client.get("/api/feedback/?field_name=fire_rating").data["count"] == 1

    def test_creating_records_the_current_user(self, auth_client, user):
        response = auth_client.post(
            "/api/feedback/",
            {
                "entity_type": "OPENING",
                "entity_id": str(OpeningFactory().id),
                "field_name": "handing",
                "value_before": "LH",
                "value_after": "RH",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Feedback.objects.get(id=response.data["id"]).changed_by_id == user.id

    def test_metrics_are_read_only(self, auth_client):
        """Counters are written by the pipeline, never by a client."""
        response = auth_client.post("/api/extraction-metrics/", {}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
