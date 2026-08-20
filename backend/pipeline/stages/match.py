"""
The matching engine (FR-4, §6.1).

    Deterministic and explainable. **No LLM in the accept/reject decision** — a
    match the estimator cannot interrogate is a match they will not trust, and
    NFR-2 forbids silent guessing.

**Hard constraints filter; soft constraints score.** That distinction is the whole
design. A hard constraint failing disqualifies a candidate outright *regardless of
how high the text similarity scores*, because the cost of getting it wrong is
categorically different from a pricing miss:

* A rated opening matched to an unrated item is a **code-compliance failure**.
* An ``LH`` opening matched to an ``RH``-only SKU is a **functional failure**
  discovered on site — handed parts are separate SKUs.
* A Division 10 accessory matched to a Division 08 opening is nonsense.

Every candidate stores its per-constraint verdicts individually, so a rejection
explains *which* constraint failed rather than merely scoring low. That mirrors
the estimator behaviour CBC explicitly validated: *"here are 3 close matches — is
it one of these?"*

This module replaces one that used pgvector cosine similarity against an embedding
column. That approach was unexplainable, could not express a hard constraint at
all, and referenced a field a migration had already removed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from shared.config import get_settings
from shared.enums import MatchStatus

log = logging.getLogger("cbc.match")

# ---------------------------------------------------------------------------
# Scoring weights (§6.1 soft constraints)
# ---------------------------------------------------------------------------
# Chosen to sum to 1.0 so match_confidence is directly comparable to the
# configured cut-off. Finish carries the most weight because it is the field
# estimators correct most often; stock carries the least because NR-13 already
# routes non-stock items to the manual path.

WEIGHT_FINISH = 0.30
WEIGHT_VENDOR = 0.25
WEIGHT_SIZE = 0.20
WEIGHT_TEXT = 0.15
WEIGHT_STOCK = 0.10

#: An exact manufacturer part number is a different kind of evidence from a
#: similarity score — the architect named this exact item. It short-circuits the
#: soft scoring, but NOT the hard constraints (§1.3: an explicit part callout is
#: the normal case, not a bypass).
PART_NUMBER_CONFIDENCE = 0.98


class MatchError(RuntimeError):
    """Matching cannot proceed for this opening."""


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchCriteria:
    """
    What we are matching *for*, normalised away from the source model.

    Built from an ``Opening`` or from one resolved hardware-set component, so the
    same constraint machinery serves both without either knowing about the other.
    """

    description: str = ""
    csi_division: str | None = "08"

    # -- hard constraint inputs -------------------------------------------
    fire_rating_minutes: int | None = None
    #: True only when the source positively says unrated. Distinct from "we did
    #: not extract a rating", which is *unresolved* and must not be matched.
    fire_rating_absent: bool = False
    handing: str | None = None
    handing_absent: bool = False

    # -- soft constraint inputs -------------------------------------------
    finish_code_id: str | None = None
    finish_base_metal: str | None = None
    width_inches: int | None = None
    height_inches: int | None = None
    vendor: str | None = None
    series: str | None = None
    part_number: str | None = None

    @classmethod
    def from_opening(cls, opening) -> MatchCriteria:
        finish = opening.finish_code
        return cls(
            description=" ".join(
                part
                for part in (opening.hardware_group, opening.size_raw, opening.finish_raw)
                if part
            ),
            csi_division="08",
            fire_rating_minutes=opening.fire_rating_minutes,
            fire_rating_absent=opening.fire_rating_absent,
            handing=opening.handing,
            handing_absent=opening.handing_absent,
            finish_code_id=str(finish.id) if finish else None,
            finish_base_metal=finish.base_metal if finish else None,
            width_inches=opening.width_inches,
            height_inches=opening.height_inches,
            # An opening's hardware_group is either a named set or an explicit
            # manufacturer part/series callout — both are normal (§1.3).
            part_number=None,
        )

    @property
    def rating_is_unresolved(self) -> bool:
        """
        Neither a rating nor a positive statement of absence.

        §5.8 forbids auto-accepting a zero-tolerance field that was never
        resolved, so this routes to manual rather than matching on the rest.
        """
        return self.fire_rating_minutes is None and not self.fire_rating_absent

    @property
    def handing_is_unresolved(self) -> bool:
        return self.handing is None and not self.handing_absent


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """One scored catalogue item, with its reasoning attached."""

    catalog_item_id: str
    vendor: str
    sku: str
    description: str

    # Hard constraints. False here is disqualifying, whatever the scores say.
    rating_ok: bool = True
    handing_ok: bool = True
    division_ok: bool = True

    # Soft constraints.
    finish_ok: bool = False
    finish_score: float = 0.0
    size_score: float = 0.0
    vendor_score: float = 0.0
    stock_score: float = 0.0
    text_score: float = 0.0

    match_confidence: float = 0.0
    is_direct_equal: bool = False
    over_specified: bool = False
    rejection_reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        """A candidate that failed any hard constraint is not a candidate."""
        return self.rating_ok and self.handing_ok and self.division_ok


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def check_rating(criteria: MatchCriteria, item) -> tuple[bool, str, bool]:
    """
    Fire rating. Returns ``(ok, reason, over_specified)``.

    * A **rated opening never matches an unrated item**, regardless of text
      similarity. Rated hardware is a distinct certified product line.
    * An **under-rated item never matches a rated opening**. A 60-minute door in a
      90-minute opening is the same failure as an unrated one.
    * Over-specification is permitted with a flag: fitting a 90-minute item in a
      60-minute opening is a cost issue, not a safety one (§6.1).
    """
    required = criteria.fire_rating_minutes
    offered = item.fire_rating_minutes

    if required is None:
        if offered is None:
            return True, "", False
        # Unrated opening, rated item — allowed, but someone is paying for a
        # certification the opening does not need.
        return True, "", True

    if offered is None:
        return (
            False,
            f"opening requires a {required}-minute rating; this item is unrated. "
            f"Rated hardware is a distinct certified product line, not a spec note.",
            False,
        )
    if offered < required:
        return (
            False,
            f"opening requires {required} minutes; this item is rated {offered}. "
            f"An under-rated assembly is a code-compliance failure.",
            False,
        )
    return True, "", offered > required


def check_handing(criteria: MatchCriteria, item) -> tuple[bool, str, bool]:
    """
    Handing. Returns ``(ok, reason, mismatch_flagged)``.

    Exact match is required when both sides are handed. ``LH`` and ``LHR`` are
    *not* interchangeable: the reverse bevel is a different SKU, and treating the
    hand alone as sufficient would put the wrong device on the quote.

    Being strict here costs false negatives, which route to the manual path — the
    designed-safe outcome under NR-13. A false positive ships the wrong lock.
    """
    required = criteria.handing
    offered = item.handing

    if offered is None:
        # The item is not handed at all; it fits either way.
        return True, "", False

    if required is None:
        if criteria.handing_absent:
            # The opening is genuinely unhanded (a pair, a slider) but the item is
            # handed. Not disqualifying, but it needs a look.
            return True, "", True
        return True, "", True

    if required != offered:
        return (
            False,
            f"opening is {required}; this SKU is {offered}-only. Handed parts are "
            f"separate SKUs.",
            False,
        )
    return True, "", False


def check_division(criteria: MatchCriteria, item) -> tuple[bool, str]:
    """
    CSI division. A Division 10 accessory never matches a Division 08 opening.

    An item with no recorded division is not a *known* mismatch, so it is not
    disqualified — but it earns nothing and is noted, because an uncategorised
    catalogue row is a data-quality problem rather than a match.
    """
    if item.csi_division is None or criteria.csi_division is None:
        return True, ""
    if item.csi_division != criteria.csi_division:
        return (
            False,
            f"opening is Division {criteria.csi_division}; this item is Division "
            f"{item.csi_division}.",
        )
    return True, ""


# ---------------------------------------------------------------------------
# Soft constraints
# ---------------------------------------------------------------------------

def score_finish(criteria: MatchCriteria, item) -> tuple[float, bool]:
    """
    Finish. Exact code highest; same base metal lower; different base metal near zero.

    **US19 and US26D never collapse.** They are different satin finishes on
    different base metals mapping to different BHMA codes. Because scoring keys on
    ``finish_code_id`` and ``base_metal`` — never on the description text — there
    is no path by which the word "satin" can bring them together (§1.3).
    """
    wanted = criteria.finish_code_id
    offered = item.finish_code

    if wanted is None:
        return 0.5, False  # nothing to judge against; neutral, not a match
    if offered is None:
        return 0.3, False

    if str(offered.id) == wanted:
        return 1.0, True
    if criteria.finish_base_metal and offered.base_metal == criteria.finish_base_metal:
        return 0.4, False
    return 0.05, False


def score_size(criteria: MatchCriteria, item, *, tolerance: int) -> float:
    """
    Size. Exact, then nearest standard, then custom.

    Most catalogue items — hinges, closers, locks — carry no size of their own, so
    this returns neutral rather than penalising them. Sizing matters for doors and
    frames, where the catalogue row does record dimensions.
    """
    if criteria.width_inches is None or criteria.height_inches is None:
        return 0.5

    item_width = getattr(item, "width_inches", None)
    item_height = getattr(item, "height_inches", None)
    if item_width is None or item_height is None:
        return 0.5

    if (item_width, item_height) == (criteria.width_inches, criteria.height_inches):
        return 1.0
    if (
        abs(item_width - criteria.width_inches) <= tolerance
        and abs(item_height - criteria.height_inches) <= tolerance
    ):
        return 0.6
    return 0.1


def score_vendor(criteria: MatchCriteria, item) -> tuple[float, bool]:
    """
    Vendor and series. Returns ``(score, is_direct_equal)``.

    A candidate from a vendor the drawing did not specify **is** a direct-equal
    proposal, and is marked as one. The system records the substitution; it never
    decides it. Choosing an equal is estimator judgment (§1.4).
    """
    if not criteria.vendor:
        return 0.5, False  # no vendor specified — the direct-equal case by default

    if item.vendor.casefold() != criteria.vendor.casefold():
        return 0.3, True

    if criteria.series and item.series:
        if item.series.casefold() == criteria.series.casefold():
            return 1.0, False
        # Hager 3400 vs 3500 is ANSI/BHMA Grade 1 vs Grade 2 — the same vendor,
        # a materially different product (§1.3).
        return 0.6, False
    return 0.9, False


def score_stock(item) -> float:
    """NR-13: automate the stock and top-N items; the estimator owns the rest."""
    return 1.0 if item.is_stock else 0.4


def score_text(criteria: MatchCriteria, item) -> float:
    """
    Description similarity — a tiebreaker, never a decider.

    Weighted lowest of the five and incapable of overturning a hard constraint,
    which is the guarantee the "regardless of how high the text similarity scores"
    tests assert.
    """
    if not criteria.description or not item.description:
        return 0.0
    return fuzz.token_set_ratio(criteria.description, item.description) / 100.0


# ---------------------------------------------------------------------------
# Scoring one candidate
# ---------------------------------------------------------------------------

def score_candidate(criteria: MatchCriteria, item, *, tolerance: int = 2) -> Candidate:
    """Evaluate one catalogue item against the criteria. Pure and deterministic."""
    candidate = Candidate(
        catalog_item_id=str(item.id),
        vendor=item.vendor,
        sku=item.sku,
        description=item.description,
    )

    reasons: list[str] = []
    candidate.rating_ok, rating_reason, candidate.over_specified = check_rating(criteria, item)
    if rating_reason:
        reasons.append(rating_reason)

    candidate.handing_ok, handing_reason, handing_flagged = check_handing(criteria, item)
    if handing_reason:
        reasons.append(handing_reason)
    if handing_flagged:
        candidate.notes.append("handed item proposed for an opening with no stated hand")

    candidate.division_ok, division_reason = check_division(criteria, item)
    if division_reason:
        reasons.append(division_reason)

    candidate.rejection_reason = " ".join(reasons)

    if candidate.over_specified:
        candidate.notes.append(
            "item is rated higher than the opening requires — a cost issue, not a safety one"
        )
    if item.csi_division is None:
        candidate.notes.append("catalogue item has no CSI division recorded")

    candidate.finish_score, candidate.finish_ok = score_finish(criteria, item)
    candidate.size_score = score_size(criteria, item, tolerance=tolerance)
    candidate.vendor_score, candidate.is_direct_equal = score_vendor(criteria, item)
    candidate.stock_score = score_stock(item)
    candidate.text_score = score_text(criteria, item)

    if not candidate.eligible:
        # A disqualified candidate scores zero. Leaving a residual score would let
        # it sort above an eligible one in any downstream code that forgot to
        # filter, which is exactly the mistake the hard/soft split exists to make
        # impossible.
        candidate.match_confidence = 0.0
        return candidate

    if (
        criteria.part_number
        and item.part_number
        and criteria.part_number.casefold() == item.part_number.casefold()
    ):
        # The architect named this exact item and every hard constraint passed.
        candidate.match_confidence = PART_NUMBER_CONFIDENCE
        candidate.notes.append("exact manufacturer part number match")
        return candidate

    candidate.match_confidence = round(
        candidate.finish_score * WEIGHT_FINISH
        + candidate.vendor_score * WEIGHT_VENDOR
        + candidate.size_score * WEIGHT_SIZE
        + candidate.text_score * WEIGHT_TEXT
        + candidate.stock_score * WEIGHT_STOCK,
        4,
    )
    return candidate


# ---------------------------------------------------------------------------
# Catalogue snapshot (bottlenecks B11 and B15)
# ---------------------------------------------------------------------------

class CatalogSnapshot:
    """
    The active catalogue, loaded once per match run.

    Reading the reference library on every match is bottleneck B15; a query per
    opening is B11. Both are solved by loading once and holding it for the
    duration of one run.

    **Deliberately not a time-based TTL.** A stale catalogue entry silently applied
    is Risk R5, so the snapshot lives for one operation and is then discarded.
    Correctness over reuse.
    """

    def __init__(self, items: list):
        self._items = items
        self._by_division: dict[str | None, list] = {}
        for item in items:
            self._by_division.setdefault(item.csi_division, []).append(item)

    @classmethod
    def load(cls, *, divisions: tuple[str, ...] | None = None) -> CatalogSnapshot:
        from catalog.models import CatalogItem

        queryset = CatalogItem.objects.filter(is_active=True).select_related("finish_code")
        if divisions:
            queryset = queryset.filter(csi_division__in=divisions)
        return cls(list(queryset))

    def candidates_for(self, criteria: MatchCriteria) -> list:
        """
        Items worth scoring.

        Pre-filtering by division is an optimisation, not the constraint: the
        division check runs again in :func:`score_candidate` so that a candidate
        supplied from anywhere else is still checked.
        """
        if criteria.csi_division is None:
            return self._items
        return [
            *self._by_division.get(criteria.csi_division, []),
            *self._by_division.get(None, []),
        ]

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# Matching one opening
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Ranked candidates for one opening, plus the disposition."""

    criteria: MatchCriteria
    accepted: list[Candidate] = field(default_factory=list)
    rejected: list[Candidate] = field(default_factory=list)
    status: str = MatchStatus.PROPOSED.value
    manual_reason: str = ""

    @property
    def best(self) -> Candidate | None:
        return self.accepted[0] if self.accepted else None


def match_criteria(
    criteria: MatchCriteria,
    snapshot: CatalogSnapshot,
    *,
    top_n: int | None = None,
    cutoff: float | None = None,
    tolerance: int | None = None,
) -> MatchResult:
    """
    Rank candidates for one set of criteria.

    Routes to the manual path — rather than proposing anything — when a
    zero-tolerance field was never resolved. §5.8 forbids auto-accepting a rating
    or handing that has not been confirmed, and matching around an unknown rating
    would do exactly that while looking like a normal result.
    """
    settings_obj = get_settings()
    top_n = top_n if top_n is not None else settings_obj.match_top_n
    cutoff = cutoff if cutoff is not None else settings_obj.match_confidence_cutoff
    tolerance = (
        tolerance if tolerance is not None else settings_obj.match_size_tolerance_inches
    )

    if criteria.rating_is_unresolved:
        return MatchResult(
            criteria=criteria,
            status=MatchStatus.MANUAL.value,
            manual_reason=(
                "fire rating was not resolved for this opening. A rating cannot be "
                "matched around: an unrated assembly in a rated opening is a "
                "code-compliance failure (§5.8). Confirm the rating first."
            ),
        )
    if criteria.handing_is_unresolved:
        return MatchResult(
            criteria=criteria,
            status=MatchStatus.MANUAL.value,
            manual_reason=(
                "handing was not resolved for this opening. Handed parts are "
                "separate SKUs, so this must be confirmed before a line is proposed."
            ),
        )

    scored = [
        score_candidate(criteria, item, tolerance=tolerance)
        for item in snapshot.candidates_for(criteria)
    ]
    eligible = sorted(
        (c for c in scored if c.eligible), key=lambda c: c.match_confidence, reverse=True
    )
    rejected = [c for c in scored if not c.eligible]

    accepted = eligible[:top_n]
    result = MatchResult(criteria=criteria, accepted=accepted, rejected=rejected)

    if not accepted:
        result.status = MatchStatus.MANUAL.value
        result.manual_reason = (
            "no catalogue item satisfies this opening's hard constraints. "
            + (rejected[0].rejection_reason if rejected else "The library has no candidates.")
        )
    elif accepted[0].match_confidence < cutoff:
        # NR-13: do not attempt to price every option permutation. The estimator
        # owns the long tail by design, not by failure.
        result.status = MatchStatus.MANUAL.value
        result.manual_reason = (
            f"best match scores {accepted[0].match_confidence:.2f}, below the "
            f"{cutoff:.2f} cut-off. Routed to the manual/custom path rather than "
            f"auto-proposing a line (NR-13)."
        )

    return result


def match_opening(opening, snapshot: CatalogSnapshot, **kwargs) -> MatchResult:
    """Convenience wrapper: build criteria from an ``Opening`` and match."""
    return match_criteria(MatchCriteria.from_opening(opening), snapshot, **kwargs)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_matches(opening, result: MatchResult) -> list:
    """
    Write ``matches`` rows for one opening.

    Rejected candidates are **not** written: with a full catalogue every opening
    would accumulate hundreds of rows recording that a grab bar is not a door.
    The one exception is a manual routing with no eligible candidate at all, where
    the reason is recorded on the opening so the estimator can see why nothing was
    proposed.
    """
    from django.db import transaction
    from openings.models import Match

    written = []
    with transaction.atomic():
        Match.objects.filter(opening=opening).delete()

        for rank, candidate in enumerate(result.accepted, start=1):
            written.append(
                Match.objects.create(
                    opening=opening,
                    catalog_item_id=candidate.catalog_item_id,
                    rank=rank,
                    match_confidence=candidate.match_confidence,
                    rating_ok=candidate.rating_ok,
                    handing_ok=candidate.handing_ok,
                    division_ok=candidate.division_ok,
                    finish_ok=candidate.finish_ok,
                    finish_score=candidate.finish_score,
                    size_score=candidate.size_score,
                    vendor_score=candidate.vendor_score,
                    stock_score=candidate.stock_score,
                    status=result.status,
                    is_direct_equal=candidate.is_direct_equal,
                    substitution_note=(
                        "proposed as a direct equal; the estimator chooses whether to "
                        "accept it (§1.4)"
                        if candidate.is_direct_equal
                        else ""
                    ),
                    rejection_reason="; ".join(candidate.notes),
                )
            )

        if result.status == MatchStatus.MANUAL.value and result.manual_reason:
            opening.review_notes = (
                f"{opening.review_notes}\n{result.manual_reason}".strip()
            )
            opening.save(update_fields=["review_notes", "updated_at"])

    return written


def match_project(project, *, snapshot: CatalogSnapshot | None = None) -> dict:
    """Match every opening on a project. Returns counters for the job record."""
    from openings.models import Opening

    snapshot = snapshot or CatalogSnapshot.load()
    counts = {"openings": 0, "proposed": 0, "manual": 0, "matches_written": 0}

    openings = Opening.objects.filter(project=project).select_related(
        "finish_code", "throat_depth"
    )
    for opening in openings:
        result = match_opening(opening, snapshot)
        written = persist_matches(opening, result)
        counts["openings"] += 1
        counts["matches_written"] += len(written)
        if result.status == MatchStatus.MANUAL.value:
            counts["manual"] += 1
        else:
            counts["proposed"] += 1

    log.info("matching complete", extra={"project_id": str(project.id), **counts})
    return counts
