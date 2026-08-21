"""
Door size parsing (§5.7, §1.3).

    Fixed 4-digit shorthand: first two digits width, last two height,
    feet-inches. ``3070`` = 3'-0" x 7'-0"; ``3670`` = 3'-6" x 7'-0". A **fixed
    parsing rule**, never inferred per document.

This is a regex, not a model call, and that is the whole point of §5.7: the rule
is right 100% of the time, can be unit-tested, and costs nothing. A model is right
most of the time, cannot be unit-tested, and costs money. Anywhere a rule exists,
the rule wins.

Non-conforming input yields ``needs_review`` with the typed fields null and the
raw string preserved. It never guesses a plausible size — a wrong opening size
propagates into a wrong frame, a wrong door, and a wrong price.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The canonical form: exactly four digits, feet-inches-feet-inches.
FOUR_DIGIT = re.compile(r"^(\d)(\d)(\d)(\d)$")

#: The explicit form, e.g. ``3'-0" x 7'-0"`` or ``3'0 X 7'0``. Supported because
#: it is unambiguous and deterministic to read — this is parsing, not inference.
#: Anything outside these two shapes is flagged rather than interpreted.
#:
#: The separator is optional. A schedule that splits SIZE across a ``W`` and an
#: ``H`` column yields the two cells joined by a space and nothing else
#: (extraction prompt v2 rule 3) — ``3' - 6" 7' - 0"``. The model must not insert
#: an ``x`` there, because grounding normalises punctuation away and compares
#: against the concatenated cited cells: the space-joined form scores 100, and an
#: inserted ``x`` scores 76.9 against a floor of 90 and is rejected.
#:
#: Both feet-inch groups are still required, so a lone ``3' - 0"`` does not parse
#: as a size — it is flagged, which is the correct answer for half a value.
EXPLICIT = re.compile(
    r"""^(\d{1,2})\s*'\s*[-\s]?\s*(\d{1,2})?\s*"?      # 3'-0"  /  3' 0
        \s*(?:[xX×]\s*)?                          # x, or nothing but whitespace
        (\d{1,2})\s*'\s*[-\s]?\s*(\d{1,2})?\s*"?$      # 7'-0"
    """,
    re.VERBOSE,
)

#: Inches must be a real inches value. 3'-14" is a typo, not a door.
MAX_INCHES = 11


@dataclass(frozen=True)
class ParsedSize:
    """Result of parsing one ``size_raw`` string."""

    raw: str
    width_inches: int | None = None
    height_inches: int | None = None
    needs_review: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.width_inches is not None and self.height_inches is not None


def _flag(raw: str, reason: str) -> ParsedSize:
    return ParsedSize(raw=raw, needs_review=True, reason=reason)


def parse_size(raw: str | None) -> ParsedSize:
    """
    Parse a size string into inches, or flag it.

    Returns typed width and height in inches on success. On failure the raw string
    is preserved and ``needs_review`` is set — **both values are stored** so an
    estimator can see exactly what the schedule said (§5.7).
    """
    if raw is None or not str(raw).strip():
        return _flag(raw or "", "no size found on this opening")

    text = str(raw).strip().upper()

    match = FOUR_DIGIT.match(re.sub(r"[\s\-]", "", text))
    if match:
        w_ft, w_in, h_ft, h_in = (int(g) for g in match.groups())
        # No inches-range check here: each component is a single digit by
        # construction, so it is always 0-9 and therefore always valid. The check
        # belongs on the explicit form below, where "3'-14\"" is expressible.
        if w_ft == 0 or h_ft == 0:
            # 0-foot width or height is a transcription error, not a door.
            return _flag(raw, f"zero feet component in {raw!r}")
        return ParsedSize(raw=raw, width_inches=w_ft * 12 + w_in, height_inches=h_ft * 12 + h_in)

    match = EXPLICIT.match(text)
    if match:
        w_ft = int(match.group(1))
        w_in = int(match.group(2) or 0)
        h_ft = int(match.group(3))
        h_in = int(match.group(4) or 0)
        if w_in > MAX_INCHES or h_in > MAX_INCHES:
            return _flag(raw, f"inches component out of range in {raw!r} (max {MAX_INCHES})")
        return ParsedSize(raw=raw, width_inches=w_ft * 12 + w_in, height_inches=h_ft * 12 + h_in)

    # Deliberately no fallback. A 5- or 6-digit code might encode thickness, or a
    # pair-of-doors width, or something this office does that CBC has not
    # described — and a plausible wrong size is worse than an honest flag.
    return _flag(
        raw,
        f"{raw!r} does not match the fixed 4-digit rule or an explicit feet-inches "
        f"size; needs estimator confirmation",
    )


def format_size(width_inches: int | None, height_inches: int | None) -> str:
    """Render inches back to feet-inches for display. Inverse of the 4-digit rule."""
    if width_inches is None or height_inches is None:
        return ""
    return (
        f"{width_inches // 12}'-{width_inches % 12}\" x "
        f"{height_inches // 12}'-{height_inches % 12}\""
    )
