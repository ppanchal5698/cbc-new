"""
Turning matched openings into a draft quote (FR-7, §6.2 step 4).

This is the last link in the chain the pipeline builds. Everything upstream —
triage, OCR, extraction, the validation gate, matching — exists so that an
estimator opens a **populated** draft rather than a grid of findings they then
re-key by hand. NFR-6's "a reviewable draft in minutes, not hours" is a claim
about this file.

Two things it deliberately does not do:

* **It does not decide.** A match below the configured cut-off produces a line
  with no catalogue item and ``needs_review`` set, not a quietly-proposed part.
  NR-13 is explicit that the estimator owns the long tail by design, not by
  failure, and a plausible wrong line is worse than an obvious empty one.
* **It does not price.** Every figure comes from
  :mod:`quotes.pricing_ops`, which is the one place the cost waterfall and the
  margin divisor live.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from openings.models import Match, Opening

from shared.enums import LineGroup, MatchStatus, QuoteStatus

log = logging.getLogger("cbc.quotes.draft")


class DraftError(ValueError):
    """The quote cannot be generated in its current state."""


def _best_match(matches: list) -> object | None:
    """
    The match a line should be built from, or None.

    An estimator's acceptance always wins over rank: FR-9 puts them in control,
    and a regeneration that overrode an explicit acceptance would be the system
    quietly disagreeing with the person responsible for the quote.

    ``MANUAL`` candidates are not eligible. Matching already decided this opening
    is below the cut-off or has an unresolved zero-tolerance field, and promoting
    one to a line would launder that decision into a proposal (§6.1, NR-13).
    """
    accepted = [m for m in matches if m.status == MatchStatus.ACCEPTED.value]
    if accepted:
        return min(accepted, key=lambda m: m.rank)
    proposed = [m for m in matches if m.status == MatchStatus.PROPOSED.value]
    return min(proposed, key=lambda m: m.rank) if proposed else None


def _group_for(catalog_item) -> str:
    """
    Which block of the quote a line belongs in (FR-7).

    Read off the catalogue item rather than inferred, so a Division 10 accessory
    lands in the restroom block because the library says it is one — not because
    a string matched.
    """
    if catalog_item is not None and catalog_item.line_group:
        return catalog_item.line_group
    return LineGroup.DOOR.value


def _describe(opening, component=None, match=None) -> str:
    """A line description an estimator can read without opening the drawing."""
    if match is not None and match.catalog_item is not None:
        item = match.catalog_item
        return f"{item.vendor} {item.sku} — {item.description}"
    if component is not None:
        return " ".join(
            part
            for part in (
                component.description,
                component.manufacturer,
                component.part_number,
                component.finish_raw,
            )
            if part
        )
    return " ".join(
        part
        for part in (
            f"Door {opening.door_number}",
            opening.size_raw,
            opening.handing,
            opening.finish_raw,
        )
        if part
    )


def generate_lines(quote, *, replace: bool = False, as_of: date | None = None):
    """
    Build this quote's lines from its project's openings and matches, then price it.

    Ordering is by door, with that door's hardware immediately beneath it, which
    is how CBC's own workbook reads and what FR-7's "grouped by door" means.

    ``replace=False`` refuses to run against a quote that already has lines. That
    is not timidity — an estimator's edits are the most valuable data in the
    system (FR-13), and silently rebuilding over them is the one way this function
    could destroy work. ``replace=True`` is the explicit regenerate action, and it
    still only removes lines this generator owns: hand-added and free-form lines
    are left exactly where they are.
    """
    from django.db import transaction

    from .models import QuoteLine
    from .pricing_ops import assemble_quote

    if quote.status != QuoteStatus.DRAFT.value:
        raise DraftError(
            f"quote is {quote.status}; lines can only be generated while it is DRAFT (NFR-1)"
        )

    generated = QuoteLine.objects.filter(quote=quote, opening__isnull=False)
    if generated.exists() and not replace:
        raise DraftError(
            "this quote already has generated lines. Pass replace=true to rebuild them; "
            "hand-added lines are kept either way."
        )

    openings = list(
        Opening.objects.filter(project=quote.project)
        .select_related("finish_code")
        .order_by("door_number")
    )
    matches = _matches_by_opening(openings)
    components = _components_by_opening(openings)

    with transaction.atomic():
        generated.delete()

        order = 0
        flagged = 0
        for opening in openings:
            match = _best_match(matches.get(opening.id, []))
            order += 1
            flagged += _create_line(
                QuoteLine,
                quote=quote,
                opening=opening,
                match=match,
                component=None,
                line_order=order,
            )

            for component, component_match in components.get(opening.id, []):
                order += 1
                flagged += _create_line(
                    QuoteLine,
                    quote=quote,
                    opening=opening,
                    match=component_match,
                    component=component,
                    line_order=order,
                )

        _ensure_freight_line(QuoteLine, quote, line_order=order + 1)
        assemble_quote(quote, as_of=as_of)

    log.info(
        "draft quote generated",
        extra={
            "quote_id": str(quote.id),
            "openings": len(openings),
            "lines": quote.lines.count(),
            "needs_review": flagged,
            "grand_total": str(quote.grand_total),
        },
    )
    return quote


def _create_line(QuoteLine, *, quote, opening, match, component, line_order: int) -> int:
    """
    Write one line. Returns 1 if it needs estimator attention, 0 otherwise.

    A line with no catalogue item is a real line with a real quantity and no
    price — that is what "routed to the manual path" looks like on a quote, and it
    is deliberately visible rather than absent (NR-13, NFR-2).
    """
    catalog_item = match.catalog_item if match is not None else None
    quantity = Decimal("1.00")
    if component is not None and component.quantity is not None:
        quantity = component.quantity

    needs_review = catalog_item is None
    QuoteLine.objects.create(
        quote=quote,
        opening=opening,
        match=match,
        catalog_item=catalog_item,
        hardware_component=component,
        line_group=_group_for(catalog_item),
        description=_describe(opening, component=component, match=match),
        quantity=quantity,
        line_order=line_order,
        needs_review=needs_review,
        is_direct_equal=bool(match is not None and match.is_direct_equal),
        substitution_note=(match.substitution_note if match is not None else ""),
    )
    return 1 if needs_review else 0


def _ensure_freight_line(QuoteLine, quote, *, line_order: int) -> None:
    """
    Exactly one freight line, with a nullable amount (FR-7, C11).

    Freight renders ``TBD`` unless an estimator enters a value. CBC confirmed it
    is generally not quoted at estimate stage, so deriving it would invent a
    number — but FR-7 requires the line, so the line exists and is empty.
    """
    if QuoteLine.objects.filter(quote=quote, line_group=LineGroup.FREIGHT.value).exists():
        return
    QuoteLine.objects.create(
        quote=quote,
        line_group=LineGroup.FREIGHT.value,
        description="Freight",
        quantity=Decimal("1.00"),
        our_cost=quote.freight_amount or Decimal("0.0000"),
        line_order=line_order,
    )


def _matches_by_opening(openings: list) -> dict:
    """Door matches only — ``hardware_component`` is null — in one query (B11)."""
    grouped: dict = {}
    rows = Match.objects.filter(
        opening__in=openings, hardware_component__isnull=True
    ).select_related("catalog_item")
    for row in rows:
        grouped.setdefault(row.opening_id, []).append(row)
    return grouped


def _components_by_opening(openings: list) -> dict:
    """
    ``{opening_id: [(component, best_match_or_None), ...]}`` in component order.

    Matches are keyed by (opening, component) because the rating and handing hard
    constraints belong to the opening — the same HW-3 on a 90-minute door and on
    an unrated one resolve to different catalogue items (§5.8).

    Unresolved callouts are skipped here: they are already surfaced as flagged
    ``HardwareSetComponent`` rows, and putting an empty line on the quote for a
    set nobody has established the contents of would imply there is one item to
    price when there may be eight.
    """
    from openings.models import HardwareSetComponent

    by_opening: dict = {}
    groups = {o.hardware_group for o in openings if o.hardware_group}
    if not groups:
        return by_opening

    components: dict = {}
    for component in HardwareSetComponent.objects.filter(
        project__in={o.project_id for o in openings},
        hardware_group__in=groups,
        resolved=True,
    ).order_by("hardware_group", "component_index"):
        components.setdefault(component.hardware_group, []).append(component)

    component_matches: dict = {}
    for row in Match.objects.filter(
        opening__in=openings, hardware_component__isnull=False
    ).select_related("catalog_item"):
        component_matches.setdefault((row.opening_id, row.hardware_component_id), []).append(row)

    for opening in openings:
        for component in components.get(opening.hardware_group or "", []):
            match = _best_match(component_matches.get((opening.id, component.id), []))
            by_opening.setdefault(opening.id, []).append((component, match))
    return by_opening
