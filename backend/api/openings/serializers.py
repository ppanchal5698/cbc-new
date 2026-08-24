"""
Serializers for elements, provenance, openings, and matches.

Two shapes exist for provenance on purpose (bottleneck B12):

* :class:`FieldProvenanceGridSerializer` reads **one table**. The openings grid is
  the primary screen and joining
  ``field_provenance -> field_provenance_elements -> doc_elements`` for every
  field of every opening is the fan-out the specification calls out by name. The
  denormalised ``page_number`` and union bbox make that join unnecessary.
* :class:`FieldProvenanceDetailSerializer` traverses the join, and is used only
  when an estimator opens one field to see its source.
"""

from rest_framework import serializers

from .models import (
    DocElement,
    ExtractionRun,
    FieldProvenance,
    HardwareSetComponent,
    Match,
    Opening,
)


class DocElementSerializer(serializers.ModelSerializer):
    """
    One OCR element.

    The polygon is returned as 0-1 page fractions, which map directly to CSS
    percentages. That is what lets the viewer overlay the highlight client-side
    with no server-side geometry at all (bottleneck B5).
    """

    polygon = serializers.SerializerMethodField()

    class Meta:
        model = DocElement
        fields = [
            "id", "document", "element_path", "page_number", "element_type", "text",
            "polygon", "bbox_x_min", "bbox_y_min", "bbox_x_max", "bbox_y_max",
            "ocr_confidence", "reading_order",
            "table_id", "row_index", "col_index", "column_header",
        ]
        read_only_fields = fields

    def get_polygon(self, obj) -> list[list[float]]:
        """Four vertices as ``[[x, y], ...]`` in 0-1 page fractions."""
        pts = [obj.x0, obj.y0, obj.x1, obj.y1, obj.x2, obj.y2, obj.x3, obj.y3]
        if any(p is None for p in pts):
            return []
        return [[pts[i], pts[i + 1]] for i in range(0, 8, 2)]


class FieldProvenanceGridSerializer(serializers.ModelSerializer):
    """Single-table read for the openings grid. Never traverses the citation join."""

    class Meta:
        model = FieldProvenance
        fields = [
            "id", "field_name", "extracted_value",
            "ocr_confidence", "llm_confidence", "completeness_penalty",
            "final_confidence", "grounding_score",
            "page_number", "bbox_x_min", "bbox_y_min", "bbox_x_max", "bbox_y_max",
            "review_state", "rejection_reason",
        ]
        read_only_fields = fields


class FieldProvenanceDetailSerializer(serializers.ModelSerializer):
    """Full provenance including cited elements. Detail view only."""

    source_elements = serializers.SerializerMethodField()

    class Meta:
        model = FieldProvenance
        fields = [
            "id", "extraction_run", "opening", "field_name", "extracted_value",
            "ocr_confidence", "llm_confidence", "completeness_penalty",
            "final_confidence", "grounding_score",
            "page_number", "bbox_x_min", "bbox_y_min", "bbox_x_max", "bbox_y_max",
            "review_state", "rejection_reason", "source_elements",
            "created_at", "updated_at",
        ]
        read_only_fields = [f for f in fields if f not in ("extracted_value", "review_state")]

    def get_source_elements(self, obj) -> list[dict]:
        return DocElementSerializer(obj.cited_elements, many=True).data


class FieldProvenanceOverrideSerializer(serializers.Serializer):
    """An estimator correcting one extracted field (FR-9, FR-13)."""

    extracted_value = serializers.CharField(allow_null=True, allow_blank=True)
    review_state = serializers.ChoiceField(
        choices=["CONFIRMED", "CORRECTED", "REJECTED", "FLAGGED"], default="CORRECTED"
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ExtractionRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionRun
        fields = [
            "id", "document", "model_id", "model_id_cheap", "prompt_version",
            "inference_params", "ocr_result_version_id", "route_config_version",
            "status", "error_detail",
            "input_tokens", "output_tokens", "cached_input_tokens", "cost_usd",
            "started_at", "completed_at",
        ]
        read_only_fields = fields


class MatchSerializer(serializers.ModelSerializer):
    """
    One ranked candidate.

    Per-constraint verdicts are exposed individually so the UI can say *which*
    constraint failed rather than showing a low score with no explanation (§6.1).
    """

    catalog_vendor = serializers.CharField(source="catalog_item.vendor", read_only=True)
    catalog_sku = serializers.CharField(source="catalog_item.sku", read_only=True)
    catalog_description = serializers.CharField(source="catalog_item.description", read_only=True)

    class Meta:
        model = Match
        fields = [
            "id", "opening", "hardware_component", "catalog_item", "catalog_vendor", "catalog_sku",
            "catalog_description", "rank", "match_confidence", "status",
            "rating_ok", "handing_ok", "division_ok", "finish_ok",
            "finish_score", "size_score", "vendor_score", "stock_score",
            "is_direct_equal", "substitution_note", "rejection_reason", "created_at",
        ]
        read_only_fields = [
            f for f in fields if f not in ("status", "is_direct_equal", "substitution_note")
        ]


class HardwareSetComponentSerializer(serializers.ModelSerializer):
    """
    One component of a resolved hardware set (§5.11).

    ``resolved=False`` rows are returned like any other. A callout the system
    could not resolve is a finding the estimator has to act on, and hiding it
    would leave the opening pointing at a set that silently produces no lines.
    """

    class Meta:
        model = HardwareSetComponent
        fields = [
            "id", "project", "extraction_run", "hardware_group", "component_index",
            "resolved", "explicit_part", "description", "manufacturer", "part_number",
            "finish_raw", "quantity_raw", "quantity", "review_state", "review_notes",
            "created_at", "updated_at",
        ]
        read_only_fields = [f for f in fields if f not in ("review_state", "review_notes")]


class OpeningSerializer(serializers.ModelSerializer):
    """
    One opening with its per-field provenance, grid-shaped.

    ``fire_rating_absent`` and ``handing_absent`` are surfaced explicitly. FR-8
    requires flagging *missing* ratings, and a null cannot distinguish "absent",
    "not yet extracted", and "extraction rejected" — three states the estimator
    must be able to tell apart.
    """

    provenance = FieldProvenanceGridSerializer(many=True, read_only=True)
    finish_us_code = serializers.CharField(source="finish_code.us_code", read_only=True, default=None)
    finish_bhma_code = serializers.CharField(source="finish_code.bhma_code", read_only=True, default=None)

    class Meta:
        model = Opening
        fields = [
            "id", "project", "extraction_run", "door_number",
            "size_raw", "width_inches", "height_inches",
            "handing", "handing_absent",
            "finish_raw", "finish_code", "finish_us_code", "finish_bhma_code",
            "fire_rating_raw", "fire_rating_minutes", "fire_rating_absent",
            "fire_rating_source_location",
            "hardware_group", "alternate_designation", "bid_alternate",
            "wall_type", "throat_depth",
            "review_state", "review_notes", "provenance",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            f for f in fields if f not in ("review_state", "review_notes")
        ]


class SourceRegionSerializer(serializers.Serializer):
    """
    What the source viewer needs to draw one highlight.

    A CDN URL plus 0-1 polygons. No server-side cropping and no second inference:
    "show me the source" is a database join (§5.1, bottleneck B5).
    """

    page_number = serializers.IntegerField()
    raster_url = serializers.CharField(allow_null=True)
    page_width_pt = serializers.FloatField(allow_null=True)
    page_height_pt = serializers.FloatField(allow_null=True)
    rotation = serializers.IntegerField()
    polygons = serializers.ListField(child=serializers.ListField(child=serializers.ListField()))
    bbox = serializers.DictField(allow_null=True)
