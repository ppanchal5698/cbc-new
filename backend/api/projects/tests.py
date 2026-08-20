"""
Projects, the verified intake path, and the manifest API (§3.3, §4.1, §11.3).

The intake path is the one part of the system the specification calls "correct and
stays" — magic-byte verification, checksum matching, SSE, S3 version-ID capture,
write-once, and an idempotent completion step. These tests hold those guarantees.
"""

import io
import uuid

import pytest
from django.db import IntegrityError, transaction
from factories import (
    DocumentFactory,
    DocumentManifestFactory,
    PipelineJobFactory,
    ProjectFactory,
)
from rest_framework import status

from projects.models import Document
from projects.queue_ops import compute_idempotency_key
from projects.storage_ops import UploadRejected, sha256_hex, verify_pdf_bytes
from shared.enums import DocumentStatus, OCRRoute
from shared.s3_keys import assert_not_derived, get_source_document_key, is_source_key

pytestmark = pytest.mark.django_db

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


# ---------------------------------------------------------------------------
# Access control (§11.2, NFR-4)
# ---------------------------------------------------------------------------

class TestAccessControl:
    def test_unauthenticated_list_is_refused(self, api_client):
        """
        Every endpoint touches customer drawings or pricing.

        This previously returned 201 for an anonymous POST and the old test
        asserted that as correct behaviour.
        """
        response = api_client.get("/api/projects/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_unauthenticated_create_is_refused(self, api_client):
        response = api_client.post(
            "/api/projects/",
            {"name": "x", "source_channel": "MANUAL", "initiator_email": "a@b.test"},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_health_is_the_only_open_endpoint(self, api_client):
        assert api_client.get("/api/health/").status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

class TestProjectCrud:
    def test_create_returns_201_with_the_id(self, auth_client):
        """A create response without the id gives the client nothing to act on."""
        response = auth_client.post(
            "/api/projects/",
            {
                "name": "McDonald's Dayton Remodel",
                "source_channel": "MANUAL",
                "initiator_email": "kellan@cbc.test",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert uuid.UUID(response.data["id"])
        assert response.data["name"] == "McDonald's Dayton Remodel"

    @pytest.mark.parametrize(
        "payload,missing",
        [
            ({"source_channel": "MANUAL", "initiator_email": "a@cbc.test"}, "name"),
            ({"name": "x", "source_channel": "MANUAL"}, "initiator_email"),
        ],
    )
    def test_required_fields_are_enforced(self, auth_client, payload, missing):
        response = auth_client.post("/api/projects/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert missing in response.data

    def test_initiator_email_must_be_an_email(self, auth_client):
        """FR-10 routes the quote here; a malformed address loses the quote."""
        response = auth_client.post(
            "/api/projects/",
            {"name": "x", "source_channel": "MANUAL", "initiator_email": "not-an-email"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_source_channel_is_constrained_to_the_enum(self, auth_client):
        response = auth_client.post(
            "/api/projects/",
            {"name": "x", "source_channel": "PIGEON", "initiator_email": "a@cbc.test"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_is_flat_not_double_prefixed(self, auth_client):
        ProjectFactory.create_batch(3)
        response = auth_client.get("/api/projects/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3

    def test_list_is_paginated(self, auth_client):
        ProjectFactory.create_batch(3)
        body = auth_client.get("/api/projects/").data
        assert {"count", "next", "previous", "results"} <= set(body)

    def test_detail_nests_documents(self, auth_client):
        project = ProjectFactory()
        DocumentFactory.create_batch(2, project=project)
        response = auth_client.get(f"/api/projects/{project.id}/")
        assert len(response.data["documents"]) == 2

    def test_delete_cascades_to_documents(self, auth_client):
        project = ProjectFactory()
        DocumentFactory(project=project)
        assert auth_client.delete(f"/api/projects/{project.id}/").status_code == 204
        assert Document.objects.count() == 0


# ---------------------------------------------------------------------------
# The verified intake path (§3.3 step 2, §11.3)
# ---------------------------------------------------------------------------

class TestUploadVerification:
    def test_magic_bytes_are_checked_not_the_extension(self):
        """An extension is a claim by the uploader, not evidence."""
        with pytest.raises(UploadRejected) as exc:
            verify_pdf_bytes(b"MZ\x90\x00 a PE binary", declared_name="invoice.pdf")
        assert "not a PDF" in str(exc.value)

    def test_empty_upload_is_refused(self):
        with pytest.raises(UploadRejected):
            verify_pdf_bytes(b"")

    def test_oversized_upload_is_refused_before_s3(self):
        """Textract's own async ceiling is 500 MB; beyond that is a mis-upload."""
        from projects.storage_ops import MAX_UPLOAD_BYTES

        with pytest.raises(UploadRejected) as exc:
            verify_pdf_bytes(b"%PDF-" + b"0" * MAX_UPLOAD_BYTES)
        assert "limit" in str(exc.value)

    def test_a_real_pdf_passes(self):
        verify_pdf_bytes(MINIMAL_PDF, declared_name="bid.pdf")

    def test_upload_endpoint_rejects_a_non_pdf(self, auth_client):
        project = ProjectFactory()
        fake = io.BytesIO(b"MZ\x90\x00 not a pdf")
        fake.name = "evil.pdf"
        response = auth_client.post(
            f"/api/projects/{project.id}/documents/", {"file": fake}, format="multipart"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Nothing must reach the write-once, Object-Locked source bucket.
        assert Document.objects.count() == 0


class TestSourceKeys:
    def test_key_template_is_project_and_document_scoped(self):
        """A project rename must never rewrite a source key."""
        key = get_source_document_key("proj-1", "doc-1", 1)
        assert key == "projects/proj-1/source/doc-1/v1/original.pdf"
        assert is_source_key(key)

    def test_derived_paths_are_refused_for_the_source_bucket(self):
        """§11.3: the intake guard rejecting /derived/ as an inbound path stays."""
        with pytest.raises(ValueError):
            assert_not_derived("derived/something/original.pdf")

    def test_checksum_is_sha256_hex(self):
        assert len(sha256_hex(MINIMAL_PDF)) == 64


# ---------------------------------------------------------------------------
# The Django-to-worker handoff (§3.2 rule 2, bottleneck B8)
# ---------------------------------------------------------------------------

class TestIdempotencyKey:
    def test_key_is_stable_for_identical_inputs(self):
        a = compute_idempotency_key("doc", "v1", "PREPROCESS", "cfg1")
        b = compute_idempotency_key("doc", "v1", "PREPROCESS", "cfg1")
        assert a == b and len(a) == 64

    def test_a_new_document_version_is_new_work(self):
        """A re-upload is genuinely new work and must not be deduplicated."""
        a = compute_idempotency_key("doc", "v1", "PREPROCESS", "cfg1")
        b = compute_idempotency_key("doc", "v2", "PREPROCESS", "cfg1")
        assert a != b

    def test_a_changed_routing_table_is_new_work(self):
        """
        Changing the routing table changes which pages get analysed, so the same
        PDF under a new table is a different job with a different cost.
        """
        a = compute_idempotency_key("doc", "v1", "PREPROCESS", "cfg1")
        b = compute_idempotency_key("doc", "v1", "PREPROCESS", "cfg2")
        assert a != b

    def test_stages_get_distinct_keys(self):
        """The column is globally unique; one key per stage would collide."""
        a = compute_idempotency_key("doc", "v1", "PREPROCESS", "cfg")
        b = compute_idempotency_key("doc", "v1", "OCR:TABLES,LAYOUT", "cfg")
        assert a != b


class TestPipelineJobs:
    def test_one_job_per_document_and_stage(self):
        document = DocumentFactory()
        PipelineJobFactory(document=document, stage="PREPROCESS")
        with pytest.raises(IntegrityError), transaction.atomic():
            PipelineJobFactory(document=document, stage="PREPROCESS")

    def test_status_endpoint_reads_the_same_table_the_worker_writes(self, auth_client):
        document = DocumentFactory()
        PipelineJobFactory(document=document, stage="PREPROCESS", status="COMPLETED")
        PipelineJobFactory(document=document, stage="OCR", status="STARTED")
        response = auth_client.get(f"/api/documents/{document.id}/pipeline-jobs/")
        assert response.status_code == status.HTTP_200_OK
        assert [j["stage"] for j in response.data] == ["PREPROCESS", "OCR"]


# ---------------------------------------------------------------------------
# The manifest API (§4.1, Risk R12)
# ---------------------------------------------------------------------------

class TestManifestVisibility:
    def test_every_skip_carries_a_reason(self, auth_client):
        """§4.3 design rule: never silently skip."""
        document = DocumentFactory()
        DocumentManifestFactory(
            document=document,
            page_number=7,
            page_class="DRAWING",
            ocr_route=OCRRoute.SKIP.value,
            route_reason="raster only, for the viewer",
        )
        response = auth_client.get(f"/api/documents/{document.id}/manifest/")
        page = response.data["results"][0]
        assert page["skipped"] is True
        assert page["skip_reason"]

    def test_skipped_only_filter_surfaces_unread_pages(self, auth_client):
        document = DocumentFactory()
        DocumentManifestFactory(document=document, page_number=1, ocr_route="TEXTRACT_TABLES")
        DocumentManifestFactory(document=document, page_number=2, ocr_route="SKIP")
        response = auth_client.get(
            f"/api/documents/{document.id}/manifest/?skipped_only=true"
        )
        assert response.data["count"] == 1
        assert response.data["results"][0]["page_number"] == 2

    def test_force_read_overrides_routing_and_writes_feedback(self, auth_client, user):
        """
        Risk R12 calls this a required feature, not a nice-to-have.

        The estimator's "read page 47 anyway" must both take effect and become
        training data for the anchors (FR-13).
        """
        from feedback.models import Feedback

        document = DocumentFactory()
        page = DocumentManifestFactory(
            document=document, page_number=47, ocr_route="SKIP", page_class="DRAWING"
        )
        response = auth_client.post(f"/api/manifest/{page.id}/force-read/", {}, format="json")
        assert response.status_code == status.HTTP_202_ACCEPTED

        page.refresh_from_db()
        assert page.ocr_route == "TEXTRACT_TABLES"
        assert page.forced_by_user_id == user.id

        record = Feedback.objects.get(entity_id=page.id)
        assert (record.value_before, record.value_after) == ("SKIP", "TEXTRACT_TABLES")

    def test_force_read_rejects_an_invalid_route(self, auth_client):
        page = DocumentManifestFactory()
        response = auth_client.post(
            f"/api/manifest/{page.id}/force-read/", {"ocr_route": "MAGIC"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestDocumentModel:
    def test_default_status_is_uploaded(self):
        assert DocumentFactory().status == DocumentStatus.UPLOADED.value

    def test_documents_are_not_writable_through_the_api(self, auth_client):
        """A writable file_key would let a client point a Document at any object."""
        project = ProjectFactory()
        response = auth_client.post(
            "/api/documents/",
            {"project": str(project.id), "filename": "x.pdf", "file_key": "anything"},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_manifest_page_is_unique_per_document(self):
        document = DocumentFactory()
        DocumentManifestFactory(document=document, page_number=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentManifestFactory(document=document, page_number=1)
