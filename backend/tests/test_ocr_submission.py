"""
What actually gets sent to Textract (§4.4, bottleneck B1).

Triage decides a route per page and records an estimated cost. Textract bills for
every page it *processes*. Those are two different facts, and they only meet at
the moment of submission — so a pipeline can classify perfectly, log a $0.12
estimate, and still hand Textract the whole 65-page plan set for $0.98. Every
manifest row, every log line, and the per-document budget guard would report the
triaged figure regardless.

`FAKE_OCR` cannot catch it either: the fake path synthesises only routed pages by
construction. These tests assert on the submission itself.
"""

import io
from unittest.mock import patch

import pymupdf
import pytest
from factories import DocumentFactory
from projects.models import DocumentManifest

from pipeline import coordinator
from pipeline.stages import ocr as ocr_stage
from shared.enums import OCRRoute, PageClass

pytestmark = pytest.mark.django_db


def _plan_set(pages: int) -> bytes:
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        doc.new_page(width=612, height=792).insert_text((72, 72), f"SHEET {number}")
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


@pytest.fixture
def real_ocr():
    """
    Force the real submission path.

    The suite runs with ``FAKE_OCR=1``, which is the whole reason this gap could
    exist: the fake path synthesises only routed pages by construction, so it
    reports a correctly triaged result no matter what the real path submits.
    """
    import dataclasses

    from shared.config import get_settings

    with patch.object(
        coordinator, "get_settings",
        return_value=dataclasses.replace(get_settings(), fake_ocr=False),
    ):
        yield


@pytest.fixture
def document():
    """A 65-page document whose triage routed exactly one page — page 15."""
    document = DocumentFactory(page_count=65, manifest_complete=True)
    for page in range(1, 66):
        schedule = page == 15
        DocumentManifest.objects.create(
            document=document,
            page_number=page,
            page_class=(PageClass.DOOR_SCHEDULE if schedule else PageClass.DRAWING).value,
            ocr_route=(OCRRoute.TEXTRACT_TABLES if schedule else OCRRoute.SKIP).value,
            route_reason="fixture",
        )
    return document


class TestOnlyRoutedPagesAreSubmitted:
    def test_textract_receives_a_one_page_subset_not_the_whole_plan_set(
        self, document, real_ocr
    ):
        captured = {}

        def fake_submit(*, bucket, key, **kwargs):
            captured["key"] = key
            return ocr_stage.OCRSubmission("job-1", OCRRoute.TEXTRACT_TABLES, ("TABLES",))

        def fake_put(key, body, **kwargs):
            captured.setdefault("uploads", {})[key] = body
            return "v1"

        with (
            patch("projects.storage_ops.get_source_document", return_value=_plan_set(65)),
            patch("projects.storage_ops.put_derived", side_effect=fake_put),
            patch.object(ocr_stage, "submit", side_effect=fake_submit),
        ):
            coordinator._submit_ocr(document, {}, _routing_table())

        submitted = captured["uploads"][captured["key"]]
        with pymupdf.open(stream=submitted, filetype="pdf") as subset:
            assert subset.page_count == 1, "65 pages were submitted; 1 was routed"
            assert "SHEET 15" in subset[0].get_text()

    def test_the_subset_lands_in_derived_never_source(self, document, real_ocr):
        """§4.1: the source PDF is read-only; every preprocessing output is derived."""
        captured = {}

        with (
            patch("projects.storage_ops.get_source_document", return_value=_plan_set(65)),
            patch("projects.storage_ops.put_derived", side_effect=lambda k, b, **kw: "v1"),
            patch.object(
                ocr_stage,
                "submit",
                side_effect=lambda *, bucket, key, **kw: captured.update(
                    bucket=bucket, key=key
                )
                or ocr_stage.OCRSubmission("job-1", OCRRoute.TEXTRACT_TABLES, ("TABLES",)),
            ),
        ):
            coordinator._submit_ocr(document, {}, _routing_table())

        from shared.config import get_settings

        assert captured["bucket"] == get_settings().s3_derived_bucket
        assert captured["key"] != document.file_key


class TestOversizedSubsetIsRefused:
    """
    §4.6 / C16: both source documents quoted Textract's 3,000-page limit and
    neither handled it. Triage makes it unreachable in practice, which is exactly
    why it needs a guard rather than a workflow — nobody will exercise this path.
    """

    def test_too_many_routed_pages_refuses_before_spending(self):
        with pytest.raises(ocr_stage.DocumentTooLarge, match="Nothing has been spent"):
            ocr_stage.assert_within_limits(pages=3001, size_bytes=1)

    def test_too_many_bytes_refuses_before_spending(self):
        with pytest.raises(ocr_stage.DocumentTooLarge, match="Nothing has been spent"):
            ocr_stage.assert_within_limits(pages=1, size_bytes=ocr_stage.MAX_BYTES + 1)

    def test_a_normal_bid_set_passes(self):
        ocr_stage.assert_within_limits(pages=6, size_bytes=2 * 1024 * 1024)


def _routing_table():
    from pipeline.routing import load_routing_table

    return load_routing_table()
