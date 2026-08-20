"""
Fire-rating parsing (§5.7, §5.8 — ZERO TOLERANCE).

    Fire rating: **20 / 45 / 60 / 90 minutes** (UL 10C / NFPA 252). Drives door
    core, frame, and hardware line — rated hardware is a distinct certified
    product line, not a spec note. A dropped or wrong rating is a
    **code-compliance failure, not a cosmetic error**.

Three rules govern every branch below, and none of them is negotiable:

1. **Never default to unrated.** An unrecognised string flags; it does not become
   ``None`` meaning "no rating". Those are different facts and the caller must be
   able to tell them apart.
2. **Never infer from a sibling opening.** This module sees one value at a time by
   design, so column-carry inference is structurally impossible here.
3. **Ambiguity flags.** ``B LABEL`` maps to 1 hour in some references and 1½ hours
   in others. Picking one would be inventing a safety claim, so it is reported as
   ambiguous with both candidates named.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.enums import FIRE_RATING_MINUTES

#: Bare minutes: "90", "90 MIN", "90 MINUTES", "90M".
MINUTES = re.compile(r"^(\d{1,3})\s*(?:M|MIN|MINS|MINUTE|MINUTES)?$")

#: Hours, including mixed fractions: "1-1/2 HR", "1 1/2 HOUR", "3/4 HR", "1HR".
HOURS = re.compile(
    r"^(?:(\d+)\s*[-\s]\s*)?(?:(\d+)\s*/\s*(\d+)|(\d+))\s*(?:H|HR|HRS|HOUR|HOURS)$"
)

#: NFPA 80 / UL letter labels. Values are minutes, or a tuple when the reference
#: works disagree — in which case the value is reported as ambiguous, never picked.
LETTER_LABELS: dict[str, int | tuple[int, ...]] = {
    "A": 180,          # 3 hours — outside CBC's 20/45/60/90 band
    "B": (60, 90),     # 1 hour OR 1½ hours depending on the reference. AMBIGUOUS.
    "C": 45,           # ¾ hour
    "D": 90,           # 1½ hours
    "E": 45,           # ¾ hour
}

LABEL = re.compile(r"^([A-E])\s*(?:LABEL|LBL)?$")

#: Strings that positively assert the opening is NOT rated. Distinct from an
#: empty cell, which means "nothing was written here".
NOT_RATED = {"NR", "N/R", "NONE", "NON-RATED", "NON RATED", "NOT RATED", "N.R.", "-", "--", "N/A"}


@dataclass(frozen=True)
class ParsedFireRating:
    """Result of parsing one ``fire_rating_raw`` string."""

    raw: str
    minutes: int | None = None
    #: True only when the source positively says the opening is unrated.
    #: NEVER set as a fallback for an unparseable value.
    absent: bool = False
    needs_review: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.minutes is not None or self.absent


def _flag(raw: str, reason: str) -> ParsedFireRating:
    return ParsedFireRating(raw=raw, needs_review=True, reason=reason)


def _normalise(minutes: int, raw: str) -> ParsedFireRating:
    """Accept only CBC's four valid ratings; anything else flags."""
    if minutes in FIRE_RATING_MINUTES:
        return ParsedFireRating(raw=raw, minutes=minutes)
    return _flag(
        raw,
        f"{raw!r} resolves to {minutes} minutes, which is outside CBC's rated band "
        f"{FIRE_RATING_MINUTES}. Confirm with the estimator before quoting.",
    )


def parse_fire_rating(raw: str | None) -> ParsedFireRating:
    """
    Parse a fire rating, or flag it.

    ``raw=None`` or an empty string means **nothing was extracted** — not that the
    opening is unrated. It flags, because FR-8 requires missing ratings to be
    surfaced and §5.8 forbids treating silence as a safety claim.
    """
    if raw is None or not str(raw).strip():
        return _flag(
            raw or "",
            "no fire rating found on this opening. This must be confirmed, not "
            "assumed unrated (§5.8).",
        )

    text = re.sub(r"\s+", " ", str(raw).strip().upper()).replace('"', "")

    if text in NOT_RATED:
        # The source positively says unrated. That is a finding, not a gap.
        return ParsedFireRating(raw=raw, absent=True)

    match = MINUTES.match(text)
    if match:
        return _normalise(int(match.group(1)), raw)

    match = HOURS.match(text)
    if match:
        whole, num, den, plain = match.groups()
        total = 0.0
        if whole:
            total += int(whole)
        if num and den:
            if int(den) == 0:
                return _flag(raw, f"{raw!r} contains a division by zero")
            total += int(num) / int(den)
        elif plain:
            total += int(plain)
        return _normalise(int(round(total * 60)), raw)

    match = LABEL.match(text)
    if match:
        mapped = LETTER_LABELS[match.group(1)]
        if isinstance(mapped, tuple):
            # Picking one here would invent a code-compliance claim (§5.8).
            return _flag(
                raw,
                f"{raw!r} is ambiguous: a {match.group(1)} label is "
                f"{' or '.join(str(m) for m in mapped)} minutes depending on the "
                f"reference. An estimator must confirm which applies.",
            )
        return _normalise(mapped, raw)

    return _flag(
        raw,
        f"{raw!r} is not a recognised fire rating. It is NOT being treated as "
        f"unrated — an unrated door in a rated opening is a code-compliance failure.",
    )
