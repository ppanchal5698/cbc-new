"""
Page rasterisation (§4.5, bottleneck B5).

Render each page **once**, at ingest, to the derived bucket. Never on demand.

The design this replaces rendered a PDF region at request time with PyMuPDF: a
1-3 second CPU and memory spike per click, on the same host serving every
estimator's page loads, for the feature estimators click most. Inverting it turns
a CPU-bound Python call into a CDN GET, and it is what makes 8 GiB sufficient for
the API host rather than marginal.

The polygon overlay is drawn **client-side**: ``doc_elements`` stores 0-1 page
fractions, which map directly to CSS percentages, so there is no server-side
geometry at all.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pymupdf
from PIL import Image

from shared.enums import OCRRoute, TextLayer

log = logging.getLogger("cbc.raster")


@dataclass(frozen=True)
class RasterTier:
    """One rendering tier from the §4.5 table."""

    name: str
    dpi: int
    image_format: str
    quality: int
    greyscale: bool = False


#: Input to Tier-4 Haiku classification. Small and cheap; only a minority of
#: pages ever reach it.
THUMB = RasterTier("thumb", dpi=100, image_format="JPEG", quality=70, greyscale=True)

#: What the review viewer displays, served through CloudFront with a long
#: max-age. The underlying source is immutable, so cache invalidation is never
#: needed.
VIEWER = RasterTier("viewer", dpi=150, image_format="WEBP", quality=80)

#: OCR input for VECTOR_OUTLINED pages only. Full DPI is used here and nowhere
#: else: OCR of a downsampled vector-outlined sheet loses the small annotation
#: text where door numbers and ratings live (Risk R11).
OCR_INPUT = RasterTier("ocr-input", dpi=300, image_format="PNG", quality=100)


def render_page(
    page: pymupdf.Page, tier: RasterTier, *, max_long_edge_px: int = 4000
) -> bytes:
    """
    Render one page to bytes.

    ``get_pixmap`` honours ``/Rotate`` natively, which is what keeps polygons
    aligned: a rotated sheet rendered without applying rotation produces a
    highlight 90 degrees out of place, and that is the single most common cause of
    "the highlight is in the wrong place" (§4.5).

    Arch D/E sheets (24x36", 30x42") at 300 DPI produce very large rasters, so the
    long edge is capped for the display tiers. The OCR-input tier is capped far
    higher because downsampling is exactly what Risk R11 warns against.
    """
    zoom = tier.dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    colorspace = pymupdf.csGRAY if tier.greyscale else pymupdf.csRGB
    pixmap = page.get_pixmap(matrix=matrix, alpha=False, colorspace=colorspace)

    mode = "L" if tier.greyscale else "RGB"
    image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)

    cap = max_long_edge_px if tier is not OCR_INPUT else max(max_long_edge_px, 10_000)
    if max(image.width, image.height) > cap:
        image.thumbnail((cap, cap), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    save_kwargs: dict = {"format": tier.image_format}
    if tier.image_format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = tier.quality
    if tier.image_format == "PNG":
        save_kwargs["optimize"] = True
    image.save(buffer, **save_kwargs)
    return buffer.getvalue()


def tiers_for_page(ocr_route: str, text_layer: str) -> list[RasterTier]:
    """
    Which tiers a page actually needs.

    The viewer tier is rendered for **every** page, including skipped ones: an
    estimator must be able to look at page 47 to decide whether to force a read
    (Risk R12), and that is impossible if the page was never rendered.

    The 300 DPI OCR-input tier is rendered only where it earns its cost — a
    vector-outlined page that is genuinely going to OCR.
    """
    tiers = [VIEWER]
    if ocr_route in (OCRRoute.TEXTRACT_TABLES.value, OCRRoute.TEXTRACT_TEXT.value):
        if text_layer == TextLayer.VECTOR_OUTLINED.value:
            tiers.append(OCR_INPUT)
    return tiers


def render_document(
    file_bytes: bytes,
    probes,
    *,
    max_long_edge_px: int = 4000,
    include_thumbs_for: set[int] | None = None,
):
    """
    Render every page's tiers, yielding ``(page_number, tier, image_bytes)``.

    A generator rather than a list: a 200-page set at 150 DPI is hundreds of
    megabytes of pixels, and the worker's memory profile is already the reason it
    lives on its own instance (D9). The caller uploads and discards each image.
    """
    include_thumbs_for = include_thumbs_for or set()
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        for probe in probes:
            page = doc[probe.page_number - 1]
            tiers = tiers_for_page(probe.ocr_route, probe.text_layer)
            if probe.page_number in include_thumbs_for:
                tiers.append(THUMB)
            for tier in tiers:
                try:
                    yield probe.page_number, tier, render_page(
                        page, tier, max_long_edge_px=max_long_edge_px
                    )
                except Exception:
                    # One unrenderable page must not cost the whole bid set its
                    # viewer. The manifest still records the page; raster_key stays
                    # null and the UI shows it as unavailable.
                    log.exception(
                        "failed to render page %s at %s", probe.page_number, tier.name
                    )


CONTENT_TYPES = {"JPEG": "image/jpeg", "WEBP": "image/webp", "PNG": "image/png"}
