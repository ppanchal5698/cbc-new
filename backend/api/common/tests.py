"""Configuration, enums, and the operational surface (§8.2, §8.4)."""

import dataclasses

import pytest
from rest_framework import status

from shared.config import ConfigError, get_settings
from shared.enums import CostSource, DocumentStatus, OCRRoute, PageClass, PipelineStage


class TestHealth:
    @pytest.mark.django_db
    def test_health_reports_database_reachability(self, api_client):
        body = api_client.get("/api/health/").data
        assert body["status"] == "ok"
        assert body["database"] is True


class TestConfigurationContract:
    def test_settings_are_frozen(self):
        """Configuration read once at start-up cannot drift mid-process."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            get_settings().environment = "prod"

    def test_bedrock_model_ids_are_never_defaulted(self):
        """
        C5/D12.

        ``anthropic.claude-opus-5`` is not a resolvable identifier, and a run that
        cannot name its exact model version cannot be audited.
        """
        settings_obj = dataclasses.replace(
            get_settings(), bedrock_model_id=None, bedrock_model_id_cheap=None
        )
        with pytest.raises(ConfigError) as exc:
            settings_obj.require_bedrock()
        assert "resolved at deploy" in str(exc.value)

    def test_local_llm_is_refused_outside_local(self):
        """§11.1 / open item Q2: never a second extraction path."""
        settings_obj = dataclasses.replace(get_settings(), environment="prod")
        with pytest.raises(ConfigError) as exc:
            settings_obj.require_local_llm()
        assert "local-only" in str(exc.value)

    def test_zero_tolerance_thresholds_are_stricter(self):
        """§5.8: the cost of error is categorically different."""
        settings_obj = get_settings()
        assert (
            settings_obj.confidence_threshold_fire_rating
            >= settings_obj.confidence_threshold_default
        )
        assert (
            settings_obj.confidence_threshold_handing
            >= settings_obj.confidence_threshold_default
        )

    def test_cost_guard_is_positive(self):
        """
        §10.3: the only control that catches an accidental 3,000-page upload
        before the money is gone.
        """
        assert get_settings().max_ocr_cost_per_document_usd > 0

    def test_grounding_floor_is_a_percentage(self):
        assert 0 <= get_settings().grounding_min_ratio <= 100

    def test_temperature_is_zero_for_reproducibility(self):
        """§5.4: an extraction that cannot be reproduced cannot be audited."""
        assert get_settings().bedrock_temperature == 0.0


class TestSharedEnums:
    def test_cost_waterfall_declares_its_priority_order(self):
        assert CostSource.waterfall()[0] is CostSource.P21_LAST_PO
        assert CostSource.waterfall()[-1] is CostSource.MANUAL

    def test_schedule_classes_exclude_drawings(self):
        assert PageClass.DRAWING not in PageClass.schedules()
        assert PageClass.DOOR_SCHEDULE in PageClass.schedules()

    def test_pipeline_stages_are_ordered(self):
        assert PipelineStage.PREPROCESS.index < PipelineStage.OCR.index
        assert PipelineStage.PRICE.index == len(PipelineStage.order()) - 1

    def test_django_choices_come_from_the_shared_enum(self):
        """
        §8.2: two services duplicating an enum is how READY_FOR_PROCESSING becomes
        READY in one of them.
        """
        from projects.models import Document

        choices = dict(Document._meta.get_field("status").choices)
        assert set(choices) == set(DocumentStatus.values())

    def test_ocr_routes_cover_the_cost_table(self):
        assert set(OCRRoute.values()) == {
            "TEXTRACT_TABLES", "TEXTRACT_TEXT", "NATIVE_TEXT", "SKIP",
        }

    def test_routing_table_prices_every_route(self):
        """A route with no cost entry would estimate as free and bypass the guard."""
        from decimal import Decimal

        from pipeline.routing import load_routing_table

        table = load_routing_table()
        assert table.cost_for(OCRRoute.TEXTRACT_TABLES) == Decimal("0.015")
        assert table.cost_for(OCRRoute.TEXTRACT_TEXT) == Decimal("0.0015")
        assert table.cost_for(OCRRoute.SKIP) == Decimal("0")


class TestOpenApiContract:
    @pytest.mark.django_db
    def test_schema_renders(self, auth_client):
        """§8.2: the frontend generates its types from this, never by hand (H2)."""
        response = auth_client.get("/api/schema/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["info"]["version"] == "3.0.0"

    @pytest.mark.django_db
    def test_routes_are_flat_not_double_prefixed(self, auth_client):
        paths = auth_client.get("/api/schema/").data["paths"]
        assert "/api/projects/" in paths
        assert "/api/projects/projects/" not in paths

    @pytest.mark.django_db
    def test_every_pipeline_stage_is_observable_through_the_api(self, auth_client):
        """Django's status endpoints read the same table the worker writes."""
        paths = auth_client.get("/api/schema/").data["paths"]
        assert "/api/pipeline-jobs/" in paths
        assert "/api/documents/{id}/pipeline-jobs/" in paths
