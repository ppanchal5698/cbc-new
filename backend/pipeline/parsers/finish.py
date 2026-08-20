"""
Finish-code normalisation (§5.7, NR-3, §1.3).

Two naming systems are in simultaneous use — legacy US codes and BHMA numerics —
and both must be interpreted. Lookup is **exact**, against the ``finish_codes``
table.

    **US19 and US26D must never collapse to the same row.** Estimators flagged
    this explicitly. They are different satin finishes on different base metals,
    mapping to different BHMA codes. A matcher that treats "satin" as a fuzzy
    token will conflate them.

That sentence is why there is no fuzzy fallback anywhere in this module. An
unrecognised finish flags for an estimator; it never resolves to the nearest code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_STRIP = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class ParsedFinish:
    raw: str
    finish_code_id: str | None = None
    us_code: str | None = None
    bhma_code: str | None = None
    needs_review: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.finish_code_id is not None


def normalise_token(raw: str) -> str:
    """``us 26-d`` and ``US26D`` are the same code written two ways."""
    return _STRIP.sub("", str(raw).upper())


def load_lookup() -> dict[str, tuple[str, str, str]]:
    """
    Build ``{token: (finish_code_id, us_code, bhma_code)}`` from the database.

    Both nomenclatures index the same row, so ``US26D`` and ``626`` resolve
    identically — which is the whole point of NR-3.
    """
    from pricing.models import FinishCode

    lookup: dict[str, tuple[str, str, str]] = {}
    for row in FinishCode.objects.all():
        entry = (str(row.id), row.us_code, row.bhma_code)
        lookup[normalise_token(row.us_code)] = entry
        lookup[normalise_token(row.bhma_code)] = entry
    return lookup


def parse_finish(raw: str | None, lookup: dict[str, tuple[str, str, str]]) -> ParsedFinish:
    """
    Resolve a finish string against the reference table, or flag it.

    ``lookup`` is passed in rather than rebuilt per call: a bid set parses hundreds
    of finishes and a query each would be the N+1 pattern bottleneck B11 names.
    """
    if raw is None or not str(raw).strip():
        return ParsedFinish(raw=raw or "", needs_review=True, reason="no finish found")

    hit = lookup.get(normalise_token(raw))
    if hit:
        return ParsedFinish(raw=raw, finish_code_id=hit[0], us_code=hit[1], bhma_code=hit[2])

    return ParsedFinish(
        raw=raw,
        needs_review=True,
        reason=(
            f"{raw!r} is not in the finish_codes table. It is NOT being fuzzy-matched "
            f"to the nearest code: US19 and US26D are both 'satin' and must never "
            f"collapse (§1.3)."
        ),
    )
