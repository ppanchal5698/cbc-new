"""
Customer-facing quote PDF (FR-10, C7).

HTML + CSS through WeasyPrint rather than ReportLab's imperative canvas: FR-10
requires matching CBC's *existing* customer-facing layout — a table-heavy document
exported from Excel — and CSS table layout reproduces that faster and more
maintainably. ReportLab remains the fallback if the layout ever needs vector
drawing primitives.

⚠ The final layout is blocked on open item Q10 (CBC's actual quote workbook has
not been provided). What is here is structurally correct per §6.2 step 4 — grouped
by door, a separate restroom-accessories block, a freight line, OH/KY-only tax —
and deliberately plain, so that swapping in the real layout is a template change.

**Nothing here computes money.** Every figure is read from the stored quote and
its lines. Recomputing at render time would let a PDF disagree with the record it
claims to represent (§6.2 step 5).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.template.loader import render_to_string

from shared.enums import LineGroup

log = logging.getLogger("cbc.export.pdf")

#: Standard commercial basis (§1.1). Supply-only material; Hamilton Parker PO
#: required; 30-day validity.
DEFAULT_TERMS_VERSION = "2026-08"

STANDARD_TERMS = [
    "Supply-only material. Installation labour is not included.",
    "Hamilton Parker Company purchase order required.",
    "Quotation valid for 30 days from the date above.",
    "Sales tax applies only where CBC holds nexus (Ohio and Kentucky).",
    "Freight shown as TBD is not included and will be quoted separately.",
]

GROUP_TITLES = {
    LineGroup.DOOR.value: "Doors, Frames and Hardware",
    LineGroup.RESTROOM_ACCESSORIES.value: "Restroom Accessories and Partitions",
    LineGroup.OTHER.value: "Other Items",
    LineGroup.FREIGHT.value: "Freight",
}

#: Render order. Doors first, accessories as their own block, freight last (FR-7).
GROUP_ORDER = [
    LineGroup.DOOR.value,
    LineGroup.RESTROOM_ACCESSORIES.value,
    LineGroup.OTHER.value,
]


def build_context(quote) -> dict:
    """
    Assemble the template context from **stored** values only.

    Grouping is presentational; the subtotals come off the lines, where the
    pricing engine persisted them.
    """
    lines = list(quote.lines.select_related("catalog_item", "opening").all())

    groups = []
    for group_key in GROUP_ORDER:
        members = [line for line in lines if line.line_group == group_key]
        if not members:
            continue
        groups.append(
            {
                "key": group_key,
                "title": GROUP_TITLES.get(group_key, group_key),
                # The stored group subtotal, not a re-sum: if the two ever
                # disagreed, the stored figure is the one the quote was approved on.
                "subtotal": members[0].subtotal,
                "lines": sorted(members, key=lambda item: item.line_order),
            }
        )

    freight_lines = [line for line in lines if line.line_group == LineGroup.FREIGHT.value]

    return {
        "quote": quote,
        "project": quote.project,
        "groups": groups,
        "freight_lines": freight_lines,
        # C11: freight renders TBD unless an estimator entered a value.
        "freight_display": "TBD" if quote.freight_amount is None else f"{quote.freight_amount:,.2f}",
        "subtotal_sale": quote.subtotal_sale,
        "tax_jurisdiction": quote.tax_jurisdiction,
        # Stored, so the PDF reproduces even after the rate changes.
        "tax_rate_pct": (quote.tax_rate_applied or Decimal("0")) * 100,
        "tax_amount": quote.tax_amount,
        "grand_total": quote.grand_total,
        "terms": STANDARD_TERMS,
        "terms_version": quote.terms_version or DEFAULT_TERMS_VERSION,
        "recipient_email": quote.exported_to_email or quote.project.initiator_email,
    }


def render_quote_html(quote) -> str:
    """The HTML the PDF is made from. Separated so it can be asserted in tests."""
    return render_to_string("quote_export.html", build_context(quote))


def generate_quote_pdf(quote) -> bytes:
    """
    Render the approved quote.

    WeasyPrint is imported lazily: it binds native pango/cairo libraries that are
    present in the API container but not on every developer's machine, and an
    import-time failure would take down the whole Django process rather than one
    endpoint.
    """
    from weasyprint import HTML

    html = render_quote_html(quote)
    pdf_bytes = HTML(string=html).write_pdf()
    log.info(
        "quote pdf rendered",
        extra={"quote_id": str(quote.id), "bytes": len(pdf_bytes)},
    )
    return pdf_bytes
