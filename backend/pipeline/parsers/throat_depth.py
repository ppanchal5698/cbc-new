"""
Frame throat-depth lookup (§5.7, §1.3).

    Five standard sizes cover the large majority; anything else routes to a
    manually entered custom value (cap ~10 total). **A table, not a hardcoded
    pick-list.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NORM = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class ParsedThroatDepth:
    raw: str
    throat_depth_id: str | None = None
    wall_type: str | None = None
    needs_review: bool = False
    reason: str = ""


def load_lookup() -> dict[str, tuple[str, str]]:
    from pricing.models import ThroatDepth

    return {
        _NORM.sub("", row.wall_type.upper()): (str(row.id), row.wall_type)
        for row in ThroatDepth.objects.all()
    }


def parse_throat_depth(raw: str | None, lookup: dict[str, tuple[str, str]]) -> ParsedThroatDepth:
    """Resolve a wall type to a throat depth, or route it to manual entry."""
    if raw is None or not str(raw).strip():
        return ParsedThroatDepth(raw=raw or "", needs_review=True, reason="no wall type found")

    token = _NORM.sub("", str(raw).upper())
    hit = lookup.get(token)
    if hit:
        return ParsedThroatDepth(raw=raw, throat_depth_id=hit[0], wall_type=hit[1])

    # Containment, not similarity: a schedule saying "6 IN METAL STUD W/ 5/8 GWB"
    # should find the 6" metal stud row, but nothing here can silently pick a
    # *different* depth the way a fuzzy score could.
    for key, (depth_id, wall_type) in lookup.items():
        if key and (key in token or token in key):
            return ParsedThroatDepth(raw=raw, throat_depth_id=depth_id, wall_type=wall_type)

    return ParsedThroatDepth(
        raw=raw,
        needs_review=True,
        reason=(
            f"{raw!r} is outside the five standard wall types; route to a manually "
            f"entered custom throat depth (§1.3)."
        ),
    )
