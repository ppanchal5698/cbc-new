"""
Single definition of every S3 key template (§8.2).

Both services import from here. A key built by hand anywhere else is a bug: the
source bucket is Object-Locked and write-once, so a key that does not match what
the intake path wrote is unrecoverable rather than merely wrong.

Bucket layout (§3.3, §11.3):

* **source** — versioned, Object Lock GOVERNANCE. Written once by the verified
  intake path and never mutated. Every preprocessing output lands in ``derived``.
* **derived** — versioned, no lock, lifecycle-managed. Freely rebuildable from
  source, which is what makes it safe to tier aggressively.

``version`` throughout is the *document* version (an integer that increments when
an estimator re-uploads the same logical document), not the S3 object version-ID.
The S3 version-ID is recorded separately on the ``Document`` row so a re-run is
distinguishable from an overwrite.
"""

from __future__ import annotations

# Guard: nothing may be written into the source bucket except through
# get_source_document_key. The intake path rejects any inbound key containing
# this segment (§11.3).
DERIVED_PREFIX = "derived/"
SOURCE_SEGMENT = "/source/"


# ---------------------------------------------------------------------------
# Source bucket — immutable
# ---------------------------------------------------------------------------

def get_source_document_key(project_id: str, document_id: str, version: int) -> str:
    """
    The one and only key the intake path writes (§3.3 step 2).

    Keyed by project *and* document so a project rename can never rewrite a source
    key — the existing intake test asserting this stays.
    """
    return f"projects/{project_id}/source/{document_id}/v{version}/original.pdf"


# ---------------------------------------------------------------------------
# Derived bucket — OCR artefacts
# ---------------------------------------------------------------------------

def get_ocr_result_key(document_id: str, version: int, part: int = 0) -> str:
    """
    Raw OCR JSON, gzipped, persisted immutably BEFORE any processing (§3.3 step 6).

    ``part`` is the split-part index for documents over the Textract 3,000-page /
    500 MB limit (§4.6). Part 0 is the whole document when no split was needed.
    """
    return f"{document_id}/v{version}/ocr/part{part}/ocr_result.json.gz"


def get_native_text_key(document_id: str, version: int, page: int) -> str:
    """PyMuPDF-extracted text for a NATIVE_TEXT page. Costs no OCR call (§4.4)."""
    return f"{document_id}/v{version}/native-text/{page}.json.gz"


def get_ocr_subset_key(document_id: str, version: int) -> str:
    """
    The pages triage actually routed to Textract, extracted into one small PDF.

    Textract bills per page it *processes*, not per page you care about, so the
    §4 routing decision only becomes a cost decision at the moment of submission:
    submitting the whole source PDF pays for all 200 pages of a plan set to read
    the six that carry a schedule (bottleneck B1).

    Submitted-local page N maps back to the document-global page number through
    the sorted routed-page list — see :func:`pipeline.stages.normalize.parse_blocks`.
    This also retires splitting (§4.6 / C16): a routed subset is a handful of
    pages, so the 3,000-page limit is unreachable in practice and is enforced as
    a guard rather than handled as a workflow.
    """
    return f"{document_id}/v{version}/ocr-input/subset.pdf"


def get_repaired_pdf_key(document_id: str, version: int) -> str:
    """
    A pikepdf-repaired copy of a structurally damaged source (§4.2).

    The source stays untouched; downstream stages read this and the document is
    flagged so the estimator knows a repair happened.
    """
    return f"{document_id}/v{version}/repaired/original.pdf"


# ---------------------------------------------------------------------------
# Derived bucket — page rasters (§4.5)
# ---------------------------------------------------------------------------

def get_raster_thumb_key(document_id: str, version: int, page: int) -> str:
    """100 DPI greyscale JPEG. Input to Tier-4 Haiku classification."""
    return f"{document_id}/v{version}/thumb/{page}.jpg"


def get_raster_viewer_key(document_id: str, version: int, page: int) -> str:
    """
    150 DPI WebP for the review viewer, served through CloudFront.

    Pre-rendered once at ingest so "show source" is a CDN GET rather than a
    server-side PyMuPDF crop (bottleneck B5). The underlying source is immutable,
    so these are cache-warm forever and never need invalidation.
    """
    return f"{document_id}/v{version}/page/{page}.webp"


def get_raster_ocr_input_key(document_id: str, version: int, page: int) -> str:
    """
    300 DPI PNG, rendered only for VECTOR_OUTLINED pages actually routed to OCR.

    Full DPI is used only on this tier: downsampling a vector-outlined sheet loses
    the small annotation text where door numbers and ratings live (Risk R11).
    """
    return f"{document_id}/v{version}/ocr-input/{page}.png"


# ---------------------------------------------------------------------------
# Derived bucket — quote output
# ---------------------------------------------------------------------------

def get_quote_pdf_key(project_id: str, quote_id: str, version: int) -> str:
    """Rendered customer-facing proposal (FR-10). Versioned per render."""
    return f"projects/{project_id}/quotes/{quote_id}/v{version}/quote.pdf"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def is_source_key(key: str) -> bool:
    """True when ``key`` looks like a source-bucket document key."""
    return SOURCE_SEGMENT in key and key.endswith("original.pdf")


def assert_not_derived(key: str) -> None:
    """
    Reject an inbound upload path that targets derived storage (§11.3).

    Kept as an explicit guard rather than a comment because the source bucket is
    write-once under Object Lock: a bad key there cannot be cleaned up.
    """
    if DERIVED_PREFIX in key.lower().lstrip("/"):
        raise ValueError(f"refusing to write a derived path into the source bucket: {key!r}")


__all__ = [
    "DERIVED_PREFIX",
    "SOURCE_SEGMENT",
    "get_source_document_key",
    "get_ocr_result_key",
    "get_native_text_key",
    "get_ocr_subset_key",
    "get_repaired_pdf_key",
    "get_raster_thumb_key",
    "get_raster_viewer_key",
    "get_raster_ocr_input_key",
    "get_quote_pdf_key",
    "is_source_key",
    "assert_not_derived",
]
