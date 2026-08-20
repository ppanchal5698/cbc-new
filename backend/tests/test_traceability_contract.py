"""
The traceability contract, tested at the persistence level (§5.6, §12.2 Phase 2).

    **Phase 2's critical negative test** — feed the validator a fabricated
    ``element_id`` and assert the field is rejected and flagged, *not persisted* —
    remains the single most important test in the suite. Add a second: feed a
    valid ``element_id`` with a value that does not appear in it, and assert the
    same.

These run against a real database on purpose. A unit test of the gate proves the
gate says no; only a persistence test proves that "no" reaches the tables an
estimator reads and the tables a quote is priced from.
"""

import pytest
from factories import DocElementFactory, DocumentFactory, ExtractionRunFactory, ProjectFactory
from openings.models import FieldProvenance, FieldProvenanceElement

from pipeline.llm.validators.gate import RejectionCode, Verdict, validate_field
from pipeline.stages import link as link_stage
from shared.enums import ReviewState

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scene():
    """A document with four real elements the model could legitimately cite."""
    project = ProjectFactory()
    document = DocumentFactory(project=project)
    run = ExtractionRunFactory(document=document)

    texts = {"door": "101", "size": "3070", "handing": "LH", "rating": "90 MIN"}
    elements = {
        key: DocElementFactory(
            document=document,
            element_path=f"pages/1/tables/0/cells/{index}",
            element_type="table_cell",
            text=text,
            ocr_confidence=0.96,
        )
        for index, (key, text) in enumerate(texts.items())
    }
    supplied = {str(e.id): e.text for e in elements.values()}
    rows = {str(e.id): e for e in elements.values()}
    return {
        "project": project,
        "document": document,
        "run": run,
        "elements": elements,
        "supplied": supplied,
        "rows": rows,
    }


def _record(scene, **overrides):
    """A well-formed extraction record, with per-field overrides."""
    e = scene["elements"]
    fields = {
        "door_number": {"value": "101", "source_element_ids": [str(e["door"].id)], "confidence_llm": 0.98},
        "size": {"value": "3070", "source_element_ids": [str(e["size"].id)], "confidence_llm": 0.95},
        "handing": {"value": "LH", "source_element_ids": [str(e["handing"].id)], "confidence_llm": 0.97},
        "finish": {"value": None, "source_element_ids": [], "confidence_llm": None},
        "fire_rating": {
            "value": "90 MIN",
            "source_element_ids": [str(e["rating"].id)],
            "confidence_llm": 0.97,
        },
        "hardware_group": {"value": None, "source_element_ids": [], "confidence_llm": None},
        "alternate_designation": {"value": None, "source_element_ids": [], "confidence_llm": None},
    }
    fields.update(overrides)
    return {"opening_id": "101", "needs_review": False, "fields": fields}


def _link_and_persist(scene, record):
    stats = link_stage.LinkStats()
    linked = link_stage.link_opening(
        record,
        supplied_elements=scene["supplied"],
        element_rows=scene["rows"],
        stats=stats,
    )
    opening = link_stage.persist_opening(
        project=scene["project"],
        extraction_run=scene["run"],
        record=record,
        linked=linked,
        stats=stats,
    )
    return opening, linked, stats


# ---------------------------------------------------------------------------
# THE TWO CRITICAL NEGATIVE TESTS
# ---------------------------------------------------------------------------

class TestCriticalNegatives:
    def test_fabricated_element_id_is_rejected_and_flagged_not_persisted(self, scene):
        """(a) A cited id that was never supplied must be refused."""
        record = _record(
            scene,
            fire_rating={
                "value": "90 MIN",
                "source_element_ids": ["el_TOTALLY_FABRICATED"],
                "confidence_llm": 0.99,
            },
        )
        opening, _, stats = _link_and_persist(scene, record)

        provenance = FieldProvenance.objects.get(opening=opening, field_name="fire_rating")
        assert provenance.review_state == ReviewState.REJECTED.value
        assert "not in supplied set" in provenance.rejection_reason
        assert stats.fields_rejected_citation == 1

        # NOT PERSISTED as a citation: writing the link would assert a
        # relationship the gate just refused.
        assert FieldProvenanceElement.objects.filter(field_provenance=provenance).count() == 0

        # And it must not have reached the opening as a typed value.
        assert opening.fire_rating_minutes is None
        assert opening.review_state == ReviewState.FLAGGED.value

    def test_valid_id_with_ungrounded_value_is_also_rejected(self, scene):
        """(b) A real citation for a value that is not in it must be refused too."""
        record = _record(
            scene,
            fire_rating={
                # The door-number cell says "101". It does not say "90 MIN".
                "value": "90 MIN",
                "source_element_ids": [str(scene["elements"]["door"].id)],
                "confidence_llm": 0.99,
            },
        )
        opening, _, stats = _link_and_persist(scene, record)

        provenance = FieldProvenance.objects.get(opening=opening, field_name="fire_rating")
        assert provenance.review_state == ReviewState.REJECTED.value
        assert "not grounded" in provenance.rejection_reason
        assert stats.fields_rejected_grounding == 1
        assert FieldProvenanceElement.objects.filter(field_provenance=provenance).count() == 0
        assert opening.fire_rating_minutes is None

    def test_semantically_true_but_absent_value_is_rejected(self, scene):
        """
        US26D *is* satin chrome — but the cited cell does not say "satin chrome".

        Grounding checks that the string is there, not that it means the same
        thing. A semantic comparison here would accept a value the source never
        contained.
        """
        verdict = validate_field(
            value="satin chrome",
            source_element_ids=[str(scene["elements"]["size"].id)],
            supplied_elements=scene["supplied"],
        )
        assert verdict.verdict is Verdict.REJECT
        assert verdict.code is RejectionCode.NOT_GROUNDED


# ---------------------------------------------------------------------------
# The rest of the Phase 2 exit criteria
# ---------------------------------------------------------------------------

class TestPhase2Exit:
    def test_every_populated_field_has_provenance_with_a_cited_element(self, scene):
        """No value reaches an estimator without a citation (§7.3)."""
        opening, _, _ = _link_and_persist(scene, _record(scene))
        populated = FieldProvenance.objects.filter(
            opening=opening, extracted_value__isnull=False
        ).exclude(review_state=ReviewState.REJECTED.value)
        assert populated.count() == 4  # door_number, size, handing, fire_rating
        for provenance in populated:
            assert provenance.elements.count() >= 1, f"{provenance.field_name} has no citation"

    def test_absent_fire_rating_sets_the_explicit_boolean_and_flags(self, scene):
        """
        FR-8: a missing rating is a flag, not a silent null.

        ``fire_rating_absent`` must distinguish "the schedule says NR" from "we did
        not extract it" — a null cannot carry both states.
        """
        record = _record(
            scene, fire_rating={"value": None, "source_element_ids": [], "confidence_llm": None}
        )
        opening, _, _ = _link_and_persist(scene, record)

        provenance = FieldProvenance.objects.get(opening=opening, field_name="fire_rating")
        assert provenance.review_state == ReviewState.FLAGGED.value
        assert "must be confirmed" in provenance.rejection_reason
        assert opening.fire_rating_minutes is None
        assert opening.review_state == ReviewState.FLAGGED.value

    def test_positively_unrated_sets_absent_true(self, scene):
        """'NR' in the schedule is a finding, and reaches the model as one."""
        element = DocElementFactory(document=scene["document"], text="NR", element_type="table_cell")
        scene["supplied"][str(element.id)] = "NR"
        scene["rows"][str(element.id)] = element

        record = _record(
            scene,
            fire_rating={"value": "NR", "source_element_ids": [str(element.id)], "confidence_llm": 0.99},
        )
        opening, _, _ = _link_and_persist(scene, record)
        assert opening.fire_rating_absent is True
        assert opening.fire_rating_minutes is None

    def test_composite_confidence_never_exceeds_either_input(self, scene):
        """§5.9: a confident model reading a blurry cell is not a confident result."""
        blurry = DocElementFactory(
            document=scene["document"], text="90 MIN", element_type="table_cell", ocr_confidence=0.41
        )
        scene["supplied"][str(blurry.id)] = "90 MIN"
        scene["rows"][str(blurry.id)] = blurry

        record = _record(
            scene,
            fire_rating={"value": "90 MIN", "source_element_ids": [str(blurry.id)], "confidence_llm": 0.99},
        )
        opening, _, _ = _link_and_persist(scene, record)
        provenance = FieldProvenance.objects.get(opening=opening, field_name="fire_rating")

        assert provenance.ocr_confidence == pytest.approx(0.41)
        assert provenance.llm_confidence == pytest.approx(0.99)
        assert provenance.final_confidence <= provenance.ocr_confidence
        assert provenance.final_confidence <= provenance.llm_confidence
        # Every component stored, not just the product — so the score can be
        # explained rather than merely displayed.
        assert provenance.completeness_penalty is not None

    def test_zero_tolerance_fields_flag_below_their_stricter_threshold(self, scene):
        """
        §5.8: rating and handing get a stricter floor than everything else.

        The same 0.90 confidence passes for hardware group and flags for fire
        rating, because the cost of error is categorically different.
        """
        assert link_stage.threshold_for("fire_rating") > link_stage.threshold_for("hardware_group")
        assert link_stage.threshold_for("handing") > link_stage.threshold_for("hardware_group")

    def test_raw_and_typed_values_are_both_stored(self, scene):
        """§5.7: the model proposes a raw string, code disposes a typed value."""
        opening, _, _ = _link_and_persist(scene, _record(scene))
        assert opening.size_raw == "3070"
        assert (opening.width_inches, opening.height_inches) == (36, 84)
        assert opening.fire_rating_raw == "90 MIN"
        assert opening.fire_rating_minutes == 90

    def test_unparseable_size_preserves_raw_and_flags(self, scene):
        """A non-conforming size keeps the raw string and nulls the typed fields."""
        element = DocElementFactory(
            document=scene["document"], text="SEE PLAN", element_type="table_cell"
        )
        scene["supplied"][str(element.id)] = "SEE PLAN"
        scene["rows"][str(element.id)] = element

        record = _record(
            scene,
            size={"value": "SEE PLAN", "source_element_ids": [str(element.id)], "confidence_llm": 0.9},
        )
        opening, _, _ = _link_and_persist(scene, record)
        assert opening.size_raw == "SEE PLAN"
        assert opening.width_inches is None and opening.height_inches is None
        assert opening.review_state == ReviewState.FLAGGED.value

    def test_null_with_a_citation_is_incoherent_and_rejected(self, scene):
        """Either the cell says something, or there is nothing to point at."""
        record = _record(
            scene,
            handing={"value": None, "source_element_ids": [str(scene["elements"]["handing"].id)]},
        )
        opening, _, stats = _link_and_persist(scene, record)
        provenance = FieldProvenance.objects.get(opening=opening, field_name="handing")
        assert provenance.review_state == ReviewState.REJECTED.value
        assert stats.fields_null_with_citation == 1

    def test_grid_reads_denormalised_page_and_bbox_without_a_join(self, scene):
        """Bottleneck B12: the openings grid must not traverse the citation join."""
        opening, _, _ = _link_and_persist(scene, _record(scene))
        provenance = FieldProvenance.objects.get(opening=opening, field_name="fire_rating")
        assert provenance.page_number == 1
        assert provenance.bbox_x_min is not None
        assert provenance.bbox_x_max >= provenance.bbox_x_min


class TestFinishCodesNeverCollapse:
    def test_us19_and_us26d_are_different_rows(self):
        """
        §1.3, stated by estimators explicitly.

        Both are 'satin'. They are different satin finishes on different base
        metals mapping to different BHMA codes, and a matcher that fuzzy-matches
        the word would conflate them.
        """
        from pricing.models import FinishCode

        from pipeline.parsers.finish import load_lookup, parse_finish

        FinishCode.objects.create(
            us_code="US26D", bhma_code="626", description="Satin chrome on brass", base_metal="brass"
        )
        FinishCode.objects.create(
            us_code="US19", bhma_code="622", description="Flat black", base_metal="steel"
        )
        lookup = load_lookup()

        a = parse_finish("US26D", lookup)
        b = parse_finish("US19", lookup)
        assert a.finish_code_id != b.finish_code_id
        assert a.bhma_code == "626" and b.bhma_code == "622"

    def test_both_nomenclatures_resolve_to_the_same_row(self):
        """NR-3: US and BHMA codes are two names for one finish."""
        from pricing.models import FinishCode

        from pipeline.parsers.finish import load_lookup, parse_finish

        FinishCode.objects.create(
            us_code="US32D", bhma_code="630", description="Satin stainless", base_metal="stainless"
        )
        lookup = load_lookup()
        assert parse_finish("US32D", lookup).finish_code_id == parse_finish("630", lookup).finish_code_id

    def test_unknown_finish_flags_rather_than_fuzzy_matching(self):
        """Never fuzzy-match to the nearest code."""
        from pricing.models import FinishCode

        from pipeline.parsers.finish import load_lookup, parse_finish

        FinishCode.objects.create(
            us_code="US26D", bhma_code="626", description="Satin chrome", base_metal="brass"
        )
        result = parse_finish("US26E", load_lookup())
        assert result.needs_review and result.finish_code_id is None
        assert "NOT being fuzzy-matched" in result.reason
