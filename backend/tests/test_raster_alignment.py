"""
The highlight lands on the ink (§4.5, bottleneck B5).

Every other guarantee in this system is checkable by reading a row. This one is
geometric: element polygons are 0-1 fractions of the page, the review viewer draws
them as CSS percentages over a pre-rendered raster, and the two only agree if both
are in the same orientation. §4.5 calls a mismatch here "the single most common
cause of the highlight being in the wrong place", and it is invisible to every
other test in the suite — the rows are perfectly valid, they just point at the
wrong part of the page.

So this asserts what a human would otherwise check by eye: a word's box, mapped
onto the rendered page, covers darker pixels than the page average. Text is dark
and paper is white, so a box that has drifted lands on blank paper and the
brightness gives it away.

**The rotated cases are the point.** Q9 has not produced a rotated sheet, so the
golden set cannot cover one — but a rotated page can be synthesised here, and it
is what caught the real defect this test now guards: ``page.rect`` and the pixmap
are in the page's visual orientation while ``get_text`` reports the unrotated one,
so dividing one by the other put every word on a ``/Rotate 90`` sheet in the wrong
place.
"""

import io
import statistics

import pymupdf
import pytest
from PIL import Image

from pipeline.stages.fake_ocr import synthesize_page
from pipeline.stages.raster import VIEWER, render_page

#: Where the words go on the synthetic page, in unrotated PDF points.
WORDS = [
    ("DOOR", 72, 100),
    ("SCHEDULE", 160, 100),
    ("101", 72, 160),
    ("3070", 140, 160),
    ("LH", 220, 160),
    ("US26D", 280, 160),
    ("90", 360, 160),
    ("HW-3", 450, 160),
]


def _sheet(rotation: int) -> pymupdf.Document:
    """A one-page document with known text, optionally rotated like a real sheet."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for text, x, y in WORDS:
        page.insert_text((x, y), text, fontsize=14)
    if rotation:
        page.set_rotation(rotation)
    return doc


def _word_boxes(blocks: list[dict]) -> list[tuple[float, float, float, float]]:
    """The 0-1 boxes the viewer would draw, straight out of the OCR blocks."""
    boxes = []
    for block in blocks:
        if block["BlockType"] != "WORD":
            continue
        box = block["Geometry"]["BoundingBox"]
        boxes.append((box["Left"], box["Top"], box["Left"] + box["Width"], box["Top"] + box["Height"]))
    return boxes


def _render_and_read(rotation: int):
    """
    The two things the viewer puts on top of each other, from the real code paths:
    the raster from :func:`render_page`, the boxes from :func:`synthesize_page`.
    """
    doc = _sheet(rotation)
    page = doc[0]
    image = Image.open(io.BytesIO(render_page(page, VIEWER))).convert("L")
    boxes = _word_boxes(synthesize_page(page, 1, with_tables=False))
    doc.close()
    return image, boxes


def _fraction_on_ink(image: Image.Image, boxes) -> tuple[float, float, float]:
    px, py = image.size
    page_brightness = statistics.mean(image.resize((120, 120)).getdata())

    covered = []
    for x0, y0, x1, y1 in boxes:
        left, top = int(x0 * px), int(y0 * py)
        crop = image.crop((left, top, max(int(x1 * px), left + 2), max(int(y1 * py), top + 2)))
        covered.append(statistics.mean(crop.getdata()))

    on_ink = sum(1 for b in covered if b < page_brightness) / len(covered)
    return on_ink, statistics.mean(covered), page_brightness


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_word_boxes_land_on_ink(rotation):
    image, boxes = _render_and_read(rotation)
    assert len(boxes) == len(WORDS), "the synthetic sheet did not produce one block per word"

    on_ink, box_brightness, page_brightness = _fraction_on_ink(image, boxes)

    assert on_ink == 1.0, (
        f"at /Rotate {rotation}, only {on_ink:.0%} of word boxes cover darker-than-page "
        f"pixels — the polygons and the raster are not in the same orientation"
    )
    assert box_brightness < page_brightness


def test_the_check_has_teeth():
    """
    A deliberately wrong mapping must fail.

    Without this, the assertion above could be passing on a page so covered in
    ink that any box looks dark, and it would go on passing through a real
    regression.
    """
    image, boxes = _render_and_read(0)
    transposed = [(y0, x0, y1, x1) for x0, y0, x1, y1 in boxes]

    on_ink, _, _ = _fraction_on_ink(image, transposed)
    assert on_ink < 0.9
