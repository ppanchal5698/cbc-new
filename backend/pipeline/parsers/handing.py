"""
Handing parsing (§5.7, §5.8 — ZERO TOLERANCE).

    Handing: ``LH`` / ``RH`` / ``LHR`` / ``RHR``. Determines which handed lock,
    closer, or exit device is ordered — **handed parts are separate SKUs**.

Like fire rating, an unrecognised value flags rather than defaulting. A
wrong-handed lock is a functional failure discovered on site, and §6.1 makes
handing a *hard* matching constraint precisely so a scored similarity can never
talk its way past it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.enums import Handing

#: Canonical map. Keys are already punctuation- and space-stripped.
CANONICAL: dict[str, Handing] = {
    "LH": Handing.LH,
    "LEFTHAND": Handing.LH,
    "LEFT": Handing.LH,
    "L": Handing.LH,
    "RH": Handing.RH,
    "RIGHTHAND": Handing.RH,
    "RIGHT": Handing.RH,
    "R": Handing.RH,
    # Reverse-bevel variants. LHRB and LHR are the same hand; the B only says the
    # bevel is reversed, which the R already implies.
    "LHR": Handing.LHR,
    "LHRB": Handing.LHR,
    "LEFTHANDREVERSE": Handing.LHR,
    "LEFTHANDREVERSEBEVEL": Handing.LHR,
    "RHR": Handing.RHR,
    "RHRB": Handing.RHR,
    "RIGHTHANDREVERSE": Handing.RHR,
    "RIGHTHANDREVERSEBEVEL": Handing.RHR,
}

#: Strings that positively assert the opening is not handed (a pair, a sliding
#: panel). Distinct from an empty cell, which means nothing was written.
NOT_HANDED = {"NA", "NONE", "-", "--", "PAIR", "DBL", "DOUBLE"}

_STRIP = re.compile(r"[^A-Z]")


@dataclass(frozen=True)
class ParsedHanding:
    raw: str
    handing: Handing | None = None
    absent: bool = False
    needs_review: bool = False
    reason: str = ""

    @property
    def value(self) -> str | None:
        return self.handing.value if self.handing else None

    @property
    def ok(self) -> bool:
        return self.handing is not None or self.absent


def parse_handing(raw: str | None) -> ParsedHanding:
    """Parse a handing string, or flag it. Never guesses a hand."""
    if raw is None or not str(raw).strip():
        return ParsedHanding(
            raw=raw or "",
            needs_review=True,
            reason=(
                "no handing found on this opening. Absence must be confirmed, not "
                "inferred from a neighbouring row (§5.8)."
            ),
        )

    text = str(raw).strip().upper()
    if _STRIP.sub("", text.replace("/", "")) in NOT_HANDED or text in NOT_HANDED:
        return ParsedHanding(raw=raw, absent=True)

    hit = CANONICAL.get(_STRIP.sub("", text))
    if hit:
        return ParsedHanding(raw=raw, handing=hit)

    return ParsedHanding(
        raw=raw,
        needs_review=True,
        reason=(
            f"{raw!r} is not a recognised handing. Handed parts are separate SKUs, so "
            f"this must be confirmed rather than guessed."
        ),
    )
