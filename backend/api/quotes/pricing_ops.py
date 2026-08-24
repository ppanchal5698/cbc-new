"""
The pricing engine (§6.2, FR-5, FR-6, FR-7, FR-15, FR-16).

**Fully deterministic. There is no LLM call anywhere in this module.** CBC's
logic is completely specified and is replicated exactly, not approximated — it
mirrors 14 years of negotiated vendor relationships.

The arithmetic, from §1.5:

    sale_each   = our_cost / (1 - margin_pct)
    extended    = sale_each * quantity
    subtotal    = SUM(extended) per group
    grand_total = SUM(subtotal)

Only **Quantity**, **Our Cost**, and **Margin** are human-entered. Everything else
derives, and every derived figure is *persisted*: a quote issued in March must
reproduce identically in September after the margin sheet and the multiplier
sheets have both changed (§6.2 step 5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pricing.models import MarginBand, TaxRate, VendorMultiplier

from shared.config import get_settings
from shared.enums import CostSource, LineGroup

log = logging.getLogger("cbc.pricing")

CENTS = Decimal("0.01")
#: Unit prices carry four places so that extending a large quantity does not
#: accumulate a visible rounding drift against CBC's Excel workbook.
UNIT = Decimal("0.0001")


def money(value: Decimal) -> Decimal:
    """Round to cents, half-up — the convention a human doing this by hand uses."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def unit(value: Decimal) -> Decimal:
    return Decimal(value).quantize(UNIT, rounding=ROUND_HALF_UP)


class PricingError(ValueError):
    """The line cannot be priced as specified."""


# ---------------------------------------------------------------------------
# Reference-data cache (bottlenecks B11 and B15)
# ---------------------------------------------------------------------------

@dataclass
class ReferenceCache:
    """
    In-process read-through cache for the small, rarely-changing tables.

    Pricing a 40-line quote otherwise issues hundreds of queries — a per-line
    catalogue, multiplier, and margin-band lookup each (bottleneck B11). These
    tables are small and change rarely; they belong in memory for the duration of
    one assembly.

    **Deliberately not a time-based TTL** (bottleneck B15, Risk R5). A stale
    multiplier silently applied is exactly the failure NFR-10 is about, so the
    cache lives for one operation and is then discarded. Correctness over reuse.
    """

    as_of: date

    def __post_init__(self):
        self._margins: dict[str, MarginBand | None] = {}
        self._multipliers: dict[tuple[str, str], VendorMultiplier | None] = {}
        self._tax: dict[str, TaxRate | None] = {}

    def margin_band(self, band: str) -> MarginBand | None:
        if band not in self._margins:
            self._margins[band] = MarginBand.effective_on(band, self.as_of)
        return self._margins[band]

    def multiplier(self, vendor: str, tier: str | None = None) -> VendorMultiplier | None:
        key = (vendor, tier or "")
        if key not in self._multipliers:
            self._multipliers[key] = VendorMultiplier.effective_on(vendor, tier, self.as_of)
        return self._multipliers[key]

    def tax_rate(self, jurisdiction: str | None) -> TaxRate | None:
        key = (jurisdiction or "").upper()
        if key not in self._tax:
            self._tax[key] = TaxRate.effective_on(jurisdiction, self.as_of)
        return self._tax[key]


# ---------------------------------------------------------------------------
# Step 1 — cost waterfall
# ---------------------------------------------------------------------------

def resolve_cost(
    line, *, cache: ReferenceCache, as_of: date | None = None, resource: bool = False
) -> tuple[Decimal, CostSource]:
    """
    Return ``(our_cost, cost_source)`` honouring the §6.2 priority order.

    The waterfall is:

    1. ``P21_LAST_PO`` — sold within 12 months with no intervening price increase.
       Preferred **over** P21's own supplier-cost fields, which purchasing does not
       keep current.
    2. ``DISTRIBUTOR_SHEET`` — Banner/SecLock, Pionite/Wilsonart, and similar.
    3. ``MFR_LIST`` — list x the customer-specific multiplier.
    4. ``VENDOR_RFQ`` — a live quote, once it has come back.
    5. ``MANUAL`` — first-class from day one, not a fallback (Risk R3).

    **A cost already on the line is never re-sourced unless ``resource=True``.**

    Sourcing happens once, when a line is created without a cost. Re-running the
    waterfall on every recalculation would silently replace an estimator's edited
    figure the next time anything on the quote changed — and a price that moves
    underneath an estimator without their knowledge is precisely the stale-data
    failure NFR-10 is about. Refreshing a price is an explicit action (NR-2's
    "price may be out of date — refresh" prompt), never a side effect.
    """
    as_of = as_of or date.today()

    if not resource and line.our_cost and line.our_cost > 0:
        try:
            return line.our_cost, CostSource(line.cost_source)
        except ValueError:
            return line.our_cost, CostSource.MANUAL

    item = line.catalog_item

    # 1. P21 last-PO. The integration mechanism is still open (Q11/NR-10), so the
    #    hook exists and reports honestly that it found nothing rather than
    #    pretending a lookup happened.
    p21_cost = _lookup_p21_last_po(item, as_of=as_of)
    if p21_cost is not None:
        return p21_cost, CostSource.P21_LAST_PO

    # 2 & 3. List x multiplier. MAP is never used as cost — MAP governs
    #    advertising, not what CBC pays (§1.5).
    if item is not None and item.list_price is not None:
        multiplier_row = cache.multiplier(item.vendor)
        if multiplier_row is not None:
            line.list_price = item.list_price
            line.multiplier = multiplier_row.multiplier
            line.vendor_multiplier = multiplier_row
            line.multiplier_sheet_version = multiplier_row.source_sheet_version
            return unit(item.list_price * multiplier_row.multiplier), CostSource.MFR_LIST
        # No negotiated tier on file: list price is a real cost basis, but an
        # undiscounted one, so the line is flagged rather than quietly quoted.
        line.list_price = item.list_price
        line.needs_review = True
        return unit(item.list_price), CostSource.MFR_LIST

    # 5. Nothing automatic applies. This is a normal outcome, not an error: NR-13
    #    says automate the stock and top-N items and hand the long tail to the
    #    estimator with a clear cut-off.
    line.needs_review = True
    return line.our_cost or Decimal("0"), CostSource.MANUAL


def _lookup_p21_last_po(item, *, as_of: date) -> Decimal | None:
    """
    Placeholder for the P21 last-PO lookup.

    Returns ``None`` — always, today. NFR-5 fixes the *shape* (read-only, no
    write-back) but **how** the read happens is open item Q11, and NR-10 lists the
    integration as "investigate" with no conclusion.

    This function deliberately does not guess. A part-number similarity match
    against P21 is explicitly forbidden (Risk R3): item IDs diverge from
    manufacturer part numbers, and auto-accepting a near-match would put a wrong
    cost on a quote with no signal that anything was inferred.
    """
    return None


def mark_staleness(line, *, as_of: date | None = None) -> bool:
    """
    Set ``cost_is_stale`` against the configured freshness window (§6.2 step 1).

    Cost data older than 6-8 months is stale, must be discarded and refreshed. The
    window is configuration, not a constant, because CBC has not fixed it and
    there is no named data steward (Open Item 15, Risk R5).

    **No automatic silent refresh.** A price that changes underneath an estimator
    without their knowledge is exactly the stale-data failure NFR-10 is about; the
    flag surfaces NR-2's "price may be out of date — refresh" prompt instead.
    """
    as_of = as_of or date.today()
    months = get_settings().cost_freshness_months
    if line.cost_effective_date is None:
        line.cost_is_stale = False
        return False
    line.cost_is_stale = line.cost_effective_date < (as_of - timedelta(days=months * 30))
    return line.cost_is_stale


# ---------------------------------------------------------------------------
# Steps 2-3 — adders and margin
# ---------------------------------------------------------------------------

def sum_adders(line) -> Decimal:
    """
    Total the manual adders (NR-4).

    Electrification, non-removable-pin hinges, premium and lead-time finishes sit
    outside the base price book and are added on top of the sourced cost.
    """
    total = Decimal("0")
    for value in (line.adders or {}).values():
        try:
            total += Decimal(str(value))
        except Exception:  # noqa: BLE001 - operator-entered JSON
            log.warning("ignoring non-numeric adder on line %s: %r", line.id, value)
    return money(total)


def resolve_margin(line, *, cache: ReferenceCache) -> Decimal:
    """
    Return the margin to apply, respecting an override.

    Margin is applied as a **divisor, not a markup**, and has been stable for 14
    years. An override is honoured as-is: `margin_overridden` plus a recorded
    reason is the audit trail, and second-guessing the estimator here would defeat
    it.
    """
    if line.margin_overridden:
        return line.margin_pct

    item = line.catalog_item
    band_key = item.product_type_band if item is not None else None
    if band_key:
        band = cache.margin_band(band_key)
        if band is not None:
            line.margin_band = band
            return band.target_margin_pct
    return line.margin_pct


def check_floor(line, *, cache: ReferenceCache) -> bool:
    """
    Set ``below_floor_flag`` (FR-15).

    **Build the flag; build no approval workflow.** FR-15 and NFR-8 ask for
    below-floor flagging *and* approval routing, while Open Item 14 answers "no
    margin deviation today; approval routing deferred". The flag satisfies the live
    requirement without implementing something CBC deferred (C13, Risk R4).
    """
    band = line.margin_band or (
        cache.margin_band(line.catalog_item.product_type_band) if line.catalog_item else None
    )
    floor = getattr(band, "floor_margin_pct", None)
    line.below_floor_flag = bool(floor is not None and line.margin_pct < floor)
    return line.below_floor_flag


# ---------------------------------------------------------------------------
# Line pricing
# ---------------------------------------------------------------------------

def price_line(
    line,
    *,
    cache: ReferenceCache | None = None,
    as_of: date | None = None,
    resource: bool = False,
):
    """
    Compute and persist one line's derived money.

    ``resource=True`` re-runs the cost waterfall, discarding whatever is on the
    line. That is the explicit "refresh this price" action; the default preserves
    the existing cost (see :func:`resolve_cost`).

    Freight is skipped: it is a line with a nullable amount, never a computed
    number (C11).
    """
    as_of = as_of or date.today()
    cache = cache or ReferenceCache(as_of=as_of)

    if line.line_group == LineGroup.FREIGHT.value:
        # Freight renders TBD unless an estimator enters a value. Deriving it from
        # a margin would invent a number CBC does not quote at estimate stage.
        line.sale_each = line.our_cost or Decimal("0")
        line.extended = money((line.our_cost or Decimal("0")) * (line.quantity or Decimal("1")))
        line.save()
        return line

    cost, source = resolve_cost(line, cache=cache, as_of=as_of, resource=resource)
    line.our_cost = unit(cost)
    line.cost_source = source.value
    mark_staleness(line, as_of=as_of)

    line.total_adders = sum_adders(line)
    line.margin_pct = resolve_margin(line, cache=cache)

    if line.margin_pct >= 1:
        raise PricingError(
            f"margin {line.margin_pct} is 100% or more; sale_each = cost / (1 - margin) "
            f"is undefined. Margin is a divisor, not a markup (§1.5)."
        )

    base = line.our_cost + line.total_adders
    line.sale_each = unit(base / (Decimal("1") - line.margin_pct))
    line.extended = money(line.sale_each * (line.quantity or Decimal("0")))

    check_floor(line, cache=cache)
    line.save()
    return line


# ---------------------------------------------------------------------------
# Step 4 — assembly
# ---------------------------------------------------------------------------

def _subtotal_key(line) -> str:
    """
    Which block a line subtotals under.

    The catalogue's section first and the extracted item's second — the catalogue
    is the library purchasing maintains, and where the two disagree it wins. A
    line with neither falls back to its line group so it still lands somewhere
    rather than pooling with every other unsectioned line.
    """
    item = line.catalog_item
    if item is not None and item.csi_division:
        return item.csi_division
    opening = line.opening
    if opening is not None and opening.csi_division:
        return opening.csi_division
    return line.line_group


def assemble_quote(quote, *, as_of: date | None = None):
    """
    Group, subtotal, tax, and total one quote (§6.2 step 4, FR-7).

    Grouped by door with subtotals, a separate restroom-accessories block, and a
    freight line. Sales tax applies **only** to Ohio and Kentucky, read from
    ``tax_rates`` with effective dates — never from constants.

    Every figure is persisted. Nothing here is recomputed on read.
    """
    as_of = as_of or date.today()
    cache = ReferenceCache(as_of=as_of)

    # One query for every line and its catalogue item, instead of a lookup per
    # line (bottleneck B11).
    lines = list(
        quote.lines.select_related("catalog_item", "margin_band", "vendor_multiplier").all()
    )
    for line in lines:
        price_line(line, cache=cache, as_of=as_of)

    # Group subtotals, stored on each member line so the rendered PDF and any
    # later audit read the same number the engine computed.
    #
    # Keyed on **CSI section**, which is how a quote is read in the trade and how
    # both the review screen and the proposal group their blocks: Division 08
    # openings, Division 10 specialties, Division 06 finishes. That also satisfies
    # FR-7 more exactly than the internal line group did — "grouped by door with
    # a separate restroom-accessories block" *is* 08 and 10.
    #
    # The key has to match what the screens group by or the number is worse than
    # useless: it looks authoritative and describes a different set of lines.
    subtotals: dict[str, Decimal] = {}
    for line in lines:
        if line.line_group == LineGroup.FREIGHT.value:
            continue
        key = _subtotal_key(line)
        subtotals[key] = subtotals.get(key, Decimal("0")) + line.extended
    for line in lines:
        if line.line_group == LineGroup.FREIGHT.value:
            line.subtotal = Decimal("0.00")
        else:
            line.subtotal = money(subtotals.get(_subtotal_key(line), Decimal("0")))
        line.save(update_fields=["subtotal", "updated_at"])

    quote.subtotal_sale = money(sum(subtotals.values(), Decimal("0")))

    freight = quote.freight_amount
    taxable = quote.subtotal_sale + (freight or Decimal("0"))

    tax_row = cache.tax_rate(quote.tax_jurisdiction)
    quote.tax_rate_applied = tax_row.rate_pct if tax_row else None
    quote.tax_amount = money(taxable * tax_row.rate_pct) if tax_row else Decimal("0.00")

    quote.grand_total = money(taxable + quote.tax_amount)
    quote.save(
        update_fields=[
            "subtotal_sale", "tax_rate_applied", "tax_amount", "grand_total", "updated_at",
        ]
    )
    log.info(
        "quote assembled",
        extra={
            "quote_id": str(quote.id),
            "lines": len(lines),
            "grand_total": str(quote.grand_total),
            "tax_jurisdiction": quote.tax_jurisdiction,
        },
    )
    return quote


def apply_rfq_price(rfq):
    """Slot a returned vendor price into its draft line (FR-16)."""
    line = rfq.quote_line
    line.our_cost = unit(rfq.returned_price)
    line.cost_source = CostSource.VENDOR_RFQ.value
    line.cost_effective_date = (rfq.returned_at or date.today()).date() if hasattr(
        rfq.returned_at, "date"
    ) else date.today()
    line.needs_review = False
    price_line(line)
    assemble_quote(line.quote)
    return line
