"""
The openings grid, provenance, and the source viewer (FR-2, FR-8, FR-9, §5.5).

The read paths here are the ones bottleneck B12 is about: the grid is the primary
screen, and joining ``field_provenance -> field_provenance_elements ->
doc_elements`` for every field of every opening is the fan-out the specification
names. These tests assert the denormalised read stays denormalised.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from factories import (
    DocElementFactory,
    DocumentFactory,
    DocumentManifestFactory,
    ExtractionRunFactory,
    FieldProvenanceElementFactory,
    FieldProvenanceFactory,
    MatchFactory,
    OpeningFactory,
    ProjectFactory,
)
from rest_framework import status

from openings.models import Opening
from shared.enums import ReviewState

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The grid (FR-2, FR-8)
# ---------------------------------------------------------------------------

class TestOpeningsGrid:
    def test_list_is_flat_and_paginated(self, auth_client):
        OpeningFactory.create_batch(3)
        body = auth_client.get("/api/openings/").data
        assert body["count"] == 3
        assert "results" in body

    def test_filter_by_project_isolates_rows(self, auth_client):
        a, b = ProjectFactory(), ProjectFactory()
        OpeningFactory.create_batch(2, project=a)
        OpeningFactory(project=b)
        body = auth_client.get(f"/api/openings/?project={a.id}").data
        assert body["count"] == 2

    def test_absent_flags_are_exposed_distinctly_from_null(self, auth_client):
        """
        FR-8: a null cannot distinguish "absent", "not extracted", and "rejected".

        The grid must be able to show all three differently.
        """
        opening = OpeningFactory(
            fire_rating_minutes=None, fire_rating_absent=True, fire_rating_raw="NR"
        )
        body = auth_client.get(f"/api/openings/{opening.id}/").data
        assert body["fire_rating_absent"] is True
        assert body["fire_rating_minutes"] is None
        assert body["fire_rating_raw"] == "NR"

    def test_raw_and_typed_values_are_both_exposed(self, auth_client):
        """§5.7: the model proposes a raw string, code disposes a typed value."""
        opening = OpeningFactory(size_raw="3070", width_inches=36, height_inches=84)
        body = auth_client.get(f"/api/openings/{opening.id}/").data
        assert body["size_raw"] == "3070"
        assert (body["width_inches"], body["height_inches"]) == (36, 84)

    def test_fire_rating_source_location_is_recorded(self, auth_client):
        """
        §5.8: accumulates the empirical answer to Open Item 9.

        CBC has not said where ratings live on their bid sets, so the system
        observes it across real documents instead of waiting.
        """
        opening = OpeningFactory(fire_rating_source_location="FRAME_SCHEDULE")
        body = auth_client.get(f"/api/openings/{opening.id}/").data
        assert body["fire_rating_source_location"] == "FRAME_SCHEDULE"

    def test_grid_read_does_not_scale_queries_with_opening_count(self, auth_client):
        """
        Bottleneck B12.

        Ten openings with provenance must not cost ten times the queries of one.
        """
        project = ProjectFactory()
        run = ExtractionRunFactory()
        for _ in range(10):
            opening = OpeningFactory(project=project, extraction_run=run)
            for name in ("door_number", "size", "fire_rating"):
                FieldProvenanceFactory(opening=opening, extraction_run=run, field_name=name)

        with CaptureQueriesContext(connection) as queries:
            response = auth_client.get(f"/api/openings/?project={project.id}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 10
        # select_related + prefetch, not one query per opening per field.
        assert len(queries) < 15, f"{len(queries)} queries for 10 openings — fan-out is back"

    def test_needs_review_lists_flagged_and_rejected_fields(self, auth_client):
        opening = OpeningFactory()
        FieldProvenanceFactory(
            opening=opening, extraction_run=opening.extraction_run,
            field_name="fire_rating", review_state=ReviewState.FLAGGED.value,
        )
        FieldProvenanceFactory(
            opening=opening, extraction_run=opening.extraction_run,
            field_name="handing", review_state=ReviewState.AUTO.value,
        )
        body = auth_client.get(f"/api/openings/{opening.id}/needs-review/").data
        assert [f["field_name"] for f in body] == ["fire_rating"]

    def test_an_item_added_by_hand_says_so(self, auth_client):
        """
        The ledger is writable — §1.6 phase 5 puts judgment in the estimator's
        hands and a bid set never carries everything.

        What it must not do is let a hand-written line pass for an extracted one.
        It is marked MANUAL and carries no extraction run, because attributing it
        to a model run would claim a provenance it does not have.
        """
        project = ProjectFactory()
        response = auth_client.post(
            "/api/openings/",
            {"project": str(project.id), "description": "Ladder, 8 ft", "quantity": "1"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["source_kind"] == "MANUAL"
        assert response.data["extraction_run"] is None
        assert response.data["sheet_label"] == "Added by hand"

    def test_every_edit_writes_exactly_one_feedback_row(self, auth_client):
        """FR-13: the tuning dataset, and double-counting it would skew the tuning."""
        from feedback.models import Feedback

        opening = OpeningFactory(door_number="101", description="HM flush door")
        auth_client.patch(
            f"/api/openings/{opening.id}/",
            {"description": "HM flush door, 18ga galv"},
            format="json",
        )
        rows = Feedback.objects.filter(entity_id=opening.id, field_name="description")
        assert rows.count() == 1
        assert rows.first().value_after == "HM flush door, 18ga galv"

    def test_an_unchanged_field_writes_nothing(self, auth_client):
        """A save that changed nothing is not a correction to learn from."""
        from feedback.models import Feedback

        opening = OpeningFactory(door_number="101", description="HM flush door")
        auth_client.patch(
            f"/api/openings/{opening.id}/", {"description": "HM flush door"}, format="json"
        )
        assert not Feedback.objects.filter(entity_id=opening.id).exists()


# ---------------------------------------------------------------------------
# Provenance and the estimator override (FR-9, FR-13)
# ---------------------------------------------------------------------------

class TestProvenanceOverride:
    def test_override_updates_the_value_and_the_state(self, auth_client):
        provenance = FieldProvenanceFactory(field_name="fire_rating", extracted_value="90 MIN")
        response = auth_client.post(
            f"/api/provenance/{provenance.id}/override/",
            {"extracted_value": "45 MIN", "review_state": "CORRECTED", "reason": "misread"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        provenance.refresh_from_db()
        assert provenance.extracted_value == "45 MIN"
        assert provenance.review_state == ReviewState.CORRECTED.value

    def test_override_writes_a_feedback_row(self, auth_client, user):
        """
        FR-13: **every** review-UI edit writes a feedback row.

        This is simultaneously the tuning dataset, the source of new golden-set
        cases, and the empirical answer to several of CBC's open items.
        """
        from feedback.models import Feedback

        provenance = FieldProvenanceFactory(field_name="handing", extracted_value="LH")
        auth_client.post(
            f"/api/provenance/{provenance.id}/override/",
            {"extracted_value": "RH", "reason": "checked the plan"},
            format="json",
        )
        record = Feedback.objects.get(field_provenance=provenance)
        assert (record.value_before, record.value_after) == ("LH", "RH")
        assert record.changed_by_id == user.id
        assert record.reason == "checked the plan"

    def test_override_mirrors_the_value_onto_the_opening(self, auth_client):
        """The grid and any downstream pricing must read the estimator's answer."""
        opening = OpeningFactory(hardware_group="HW-1")
        provenance = FieldProvenanceFactory(
            opening=opening, extraction_run=opening.extraction_run,
            field_name="hardware_group", extracted_value="HW-1",
        )
        auth_client.post(
            f"/api/provenance/{provenance.id}/override/",
            {"extracted_value": "HW-3"},
            format="json",
        )
        opening.refresh_from_db()
        assert opening.hardware_group == "HW-3"
        assert opening.review_state == ReviewState.CORRECTED.value

    def test_provenance_is_not_writable_by_a_plain_patch(self, auth_client):
        """
        Editing must go through the override action.

        A bare PATCH would change the value without writing feedback, silently
        dropping a row from the tuning dataset.
        """
        provenance = FieldProvenanceFactory()
        response = auth_client.patch(
            f"/api/provenance/{provenance.id}/", {"extracted_value": "x"}, format="json"
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_confidence_components_are_all_exposed(self, auth_client):
        """§5.9: every component is stored so a score can be explained."""
        provenance = FieldProvenanceFactory()
        body = auth_client.get(f"/api/provenance/{provenance.id}/").data
        for key in (
            "ocr_confidence", "llm_confidence", "completeness_penalty",
            "final_confidence", "grounding_score",
        ):
            assert key in body


# ---------------------------------------------------------------------------
# The source viewer (§5.5, bottleneck B5)
# ---------------------------------------------------------------------------

class TestSourceViewer:
    def test_source_returns_the_raster_url_and_polygons(self, auth_client):
        """
        "Show me the source" is a database join, never a second inference.

        The response is a CDN URL plus 0-1 polygons; the client overlays an
        absolutely-positioned SVG. No server-side cropping.
        """
        document = DocumentFactory()
        DocumentManifestFactory(
            document=document, page_number=1, raster_key=f"{document.id}/v1/page/1.webp",
            rotation=90, width_pt=3024.0, height_pt=2160.0,
        )
        element = DocElementFactory(document=document, page_number=1, text="90 MIN")
        provenance = FieldProvenanceFactory(page_number=1)
        FieldProvenanceElementFactory(field_provenance=provenance, doc_element=element)

        body = auth_client.get(f"/api/provenance/{provenance.id}/source/").data
        assert body["page_number"] == 1
        assert body["raster_url"].endswith("/page/1.webp")
        assert len(body["polygons"]) == 1
        assert len(body["polygons"][0]) == 4
        # Rotation must reach the client: a rotated sheet whose rotation is
        # ignored overlays the highlight 90 degrees off (§4.5).
        assert body["rotation"] == 90

    def test_polygons_are_page_fractions_not_points(self, auth_client):
        """0-1 fractions map directly to CSS percentages (bottleneck B5)."""
        document = DocumentFactory()
        DocumentManifestFactory(document=document, page_number=1)
        element = DocElementFactory(document=document, page_number=1)
        provenance = FieldProvenanceFactory(page_number=1)
        FieldProvenanceElementFactory(field_provenance=provenance, doc_element=element)

        polygon = auth_client.get(f"/api/provenance/{provenance.id}/source/").data["polygons"][0]
        assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in polygon)

    def test_a_field_with_no_citation_has_nothing_to_show(self, auth_client):
        provenance = FieldProvenanceFactory()
        response = auth_client.get(f"/api/provenance/{provenance.id}/source/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

class TestDocElements:
    def test_filter_by_document(self, auth_client):
        a, b = DocumentFactory(), DocumentFactory()
        DocElementFactory.create_batch(3, document=a)
        DocElementFactory(document=b)
        assert auth_client.get(f"/api/doc-elements/?document={a.id}").data["count"] == 3

    def test_list_is_paginated_because_the_table_is_enormous(self, auth_client):
        """
        Risk R9: tens of thousands of rows per bid set even after triage.

        An unpaginated list over this table is a denial of service against our own
        API host.
        """
        DocElementFactory.create_batch(5)
        body = auth_client.get("/api/doc-elements/").data
        assert {"count", "next", "previous", "results"} <= set(body)

    def test_elements_are_read_only(self, auth_client):
        """``ocr_confidence`` must never be recomputed or overwritten (§7.2)."""
        response = auth_client.post("/api/doc-elements/", {}, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_element_path_is_exposed_for_debugging(self, auth_client):
        element = DocElementFactory(element_path="pages/3/tables/0/cells/17")
        body = auth_client.get(f"/api/doc-elements/{element.id}/").data
        assert body["element_path"] == "pages/3/tables/0/cells/17"


# ---------------------------------------------------------------------------
# Matches (FR-4, §6.1)
# ---------------------------------------------------------------------------

class TestMatches:
    def test_per_constraint_verdicts_are_exposed_individually(self, auth_client):
        """
        §6.1: a rejected match must explain *which* constraint failed, not merely
        score low.
        """
        match = MatchFactory(rating_ok=False, handing_ok=True, finish_ok=False)
        body = auth_client.get(f"/api/matches/{match.id}/").data
        assert body["rating_ok"] is False
        assert body["handing_ok"] is True
        assert body["finish_ok"] is False
        assert "division_ok" in body

    def test_accepting_a_match_records_who_and_writes_feedback(self, auth_client, user):
        from feedback.models import Feedback

        match = MatchFactory(status="PROPOSED")
        response = auth_client.post(f"/api/matches/{match.id}/accept/", {}, format="json")
        assert response.status_code == status.HTTP_200_OK
        match.refresh_from_db()
        assert match.status == "ACCEPTED"
        assert Feedback.objects.filter(entity_id=match.id, field_name="status").exists()

    def test_rejecting_with_a_substitution_note_marks_direct_equal(self, auth_client):
        """
        §1.4: the system RECORDS a substitution; it never decides one.

        Choosing an equal is estimator judgment.
        """
        match = MatchFactory(status="PROPOSED")
        auth_client.post(
            f"/api/matches/{match.id}/accept/",
            {"substitution_note": "Hager equivalent approved by GC"},
            format="json",
        )
        match.refresh_from_db()
        assert match.is_direct_equal is True
        assert "approved by GC" in match.substitution_note

    def test_matches_for_an_opening_come_back_ranked(self, auth_client):
        opening = OpeningFactory()
        MatchFactory(opening=opening, rank=2, match_confidence=0.7)
        MatchFactory(opening=opening, rank=1, match_confidence=0.9)
        body = auth_client.get(f"/api/openings/{opening.id}/matches/").data
        assert [m["rank"] for m in body] == [1, 2]


# ---------------------------------------------------------------------------
# Duplicates — the same physical item read from two documents
# ---------------------------------------------------------------------------

class TestDuplicateResolution:
    """
    An addendum reissues a schedule row and the base row is still in the list;
    a restroom plan repeats six grab bars the fixture schedule already counted.

    Both readings are real, and which one to price is a question the documents
    genuinely leave open — so the system keeps both and asks. Choosing for the
    estimator would be inventing an answer, and quietly dropping one would lose
    an item nobody knew was missing.
    """

    @pytest.fixture
    def pair(self):
        project = ProjectFactory()
        run = ExtractionRunFactory()
        base = OpeningFactory(
            project=project, extraction_run=run, door_number="102",
            description="HM door pair, mortise prep", sheet_label="A-601", cell_label="Row 5",
            source_kind="DUPLICATE",
        )
        reissue = OpeningFactory(
            project=project, extraction_run=run, door_number="102",
            description="HM door pair, mortise prep", sheet_label="Addendum 1", cell_label="Row 2",
            source_kind="DUPLICATE", duplicate_of=base,
            duplicate_note="Reissued in addendum 1",
        )
        return base, reissue

    def test_both_readings_of_one_mark_can_coexist(self, pair):
        """
        The unique constraint is keyed on where a row was read, not on its mark.
        Keying on the mark alone would make the database refuse to record the very
        thing the ledger exists to surface.
        """
        base, reissue = pair
        assert Opening.objects.filter(door_number="102").count() == 2

    def test_keep_one_drops_the_other_and_clears_the_flag(self, auth_client, pair):
        base, reissue = pair
        response = auth_client.post(f"/api/openings/{reissue.id}/keep-one/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["source_kind"] == "EXTRACTED"
        assert response.data["duplicate_of"] is None
        assert not Opening.objects.filter(id=base.id).exists()
        assert Opening.objects.filter(id=reissue.id).exists()

    def test_keep_both_keeps_two_priceable_items(self, auth_client, pair):
        base, reissue = pair
        response = auth_client.post(f"/api/openings/{reissue.id}/keep-both/")
        assert response.status_code == status.HTTP_200_OK
        assert Opening.objects.filter(door_number="102").count() == 2
        for row in Opening.objects.filter(door_number="102"):
            assert row.source_kind == "EXTRACTED"
            assert row.duplicate_of is None

    def test_resolving_is_recorded(self, auth_client, pair):
        """Which reading an estimator chose is the answer to a question the documents posed."""
        from feedback.models import Feedback

        _, reissue = pair
        auth_client.post(f"/api/openings/{reissue.id}/keep-one/")
        assert Feedback.objects.filter(
            entity_id=reissue.id, field_name="__duplicate_resolved__"
        ).exists()

    def test_an_item_cannot_be_its_own_duplicate(self):
        """Enforced in the database, because a self-referencing pair loops forever."""
        from django.db import IntegrityError, transaction

        opening = OpeningFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            Opening.objects.filter(id=opening.id).update(duplicate_of=opening.id)


class TestBulkTriage:
    def test_confirm_all_clears_only_the_ones_needing_a_look(self, auth_client):
        project = ProjectFactory()
        needs = OpeningFactory(project=project, source_kind="REVIEW")
        manual = OpeningFactory(project=project, source_kind="MANUAL")
        auth_client.post("/api/openings/confirm-all/", {"project": str(project.id)}, format="json")
        needs.refresh_from_db()
        manual.refresh_from_db()
        assert needs.source_kind == "EXTRACTED"
        assert manual.source_kind == "MANUAL", "a hand-written line was never in question"

    def test_bulk_confirm_takes_the_picked_set(self, auth_client):
        a = OpeningFactory(source_kind="REVIEW")
        b = OpeningFactory(source_kind="REVIEW")
        auth_client.post("/api/openings/bulk-confirm/", {"ids": [str(a.id)]}, format="json")
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.source_kind == "EXTRACTED"
        assert b.source_kind == "REVIEW"

    def test_bulk_remove_deletes_and_records(self, auth_client):
        from feedback.models import Feedback

        opening = OpeningFactory()
        auth_client.post("/api/openings/bulk-remove/", {"ids": [str(opening.id)]}, format="json")
        assert not Opening.objects.filter(id=opening.id).exists()
        assert Feedback.objects.filter(entity_id=opening.id, field_name="__deleted__").exists()
