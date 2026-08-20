"""
The OCR routing table, loaded from configuration (§4.4, Risk R1).

    Build the routing table as **configuration**, not as ``if`` statements, so
    [Open Item 9] is a config change rather than a code change. This is the
    concrete form of Risk R1's "make the extraction hint configuration, never a
    hardcoded column index."

The table's content hash is recorded on every ``pipeline_jobs`` row and folded
into the OCR idempotency key: changing the routing table changes which pages get
analysed, so the same PDF under a new table is genuinely different work with a
different cost, and must not be deduplicated against the previous run.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from shared.config import get_settings
from shared.enums import OCRRoute, PageClass, TextLayer

log = logging.getLogger("cbc.routing")

#: Repository root for resolving a relative OCR_ROUTE_CONFIG path.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RouteDecision:
    """What to do with one page, and why."""

    route: OCRRoute
    reason: str
    cost_estimate: Decimal


class RoutingTable:
    """Parsed ``ocr_routes.json``."""

    def __init__(self, raw: dict, *, source: str = "<memory>"):
        self._raw = raw
        self.source = source
        self.version: str = raw.get("version", "unversioned")
        self._routes: dict = raw.get("routes", {})
        self._costs: dict = raw.get("cost_per_page_usd", {})
        self._thresholds: dict = raw.get("thresholds", {})
        self.anchors: dict[str, list[str]] = raw.get("anchors", {})
        self.sheet_number_prefixes: dict[str, str] = raw.get("sheet_number_prefixes", {})

        # Hash the semantic content only. Comments and formatting must not change
        # the identity of the table, or every re-indent would invalidate every
        # cached OCR job.
        material = json.dumps(
            {
                "routes": self._routes,
                "costs": self._costs,
                "anchors": self.anchors,
                "thresholds": self._thresholds,
            },
            sort_keys=True,
        )
        self.content_hash: str = hashlib.sha256(material.encode()).hexdigest()[:16]

    # -- thresholds ---------------------------------------------------------

    def threshold(self, name: str, default: float) -> float:
        value = self._thresholds.get(name, default)
        return float(value)

    @property
    def vector_outlined_max_words(self) -> int:
        return int(self.threshold("vector_outlined_max_words", 20))

    @property
    def vector_outlined_min_paths(self) -> int:
        return int(self.threshold("vector_outlined_min_paths", 500))

    @property
    def rich_text_min_words(self) -> int:
        return int(self.threshold("rich_text_min_words", 50))

    @property
    def drawing_min_vector_paths(self) -> int:
        """Vector-path count above which an anchor-less page is a drawing, not a mystery."""
        return int(self.threshold("drawing_min_vector_paths", 500))

    @property
    def min_keyword_confidence(self) -> float:
        return self.threshold("min_keyword_confidence", 0.55)

    # -- routing ------------------------------------------------------------

    def cost_for(self, route: OCRRoute) -> Decimal:
        return Decimal(str(self._costs.get(route.value, 0)))

    def decide(self, page_class: PageClass, text_layer: TextLayer) -> RouteDecision:
        """
        Route one page.

        A schedule page always goes to Textract even when its text layer is RICH:
        word positions alone do not give cell, row, and column structure, and the
        structure is the whole reason the page matters (§4.2).

        A prose page with a rich text layer takes the native path and costs
        nothing.
        """
        entry = self._routes.get(page_class.value)
        if entry is None:
            log.warning("no routing entry for page_class=%s; defaulting to SKIP", page_class)
            return RouteDecision(
                OCRRoute.SKIP,
                f"no routing rule for {page_class.value}; not read",
                Decimal("0"),
            )

        route = OCRRoute(entry["route"])
        reason = entry.get("reason", "")

        native_alternative = entry.get("native_text_route")
        if (
            native_alternative
            and text_layer == TextLayer.RICH
            and page_class not in PageClass.schedules()
        ):
            route = OCRRoute(native_alternative)
            reason = "rich text layer present; word positions already in the file (no OCR cost)"

        # A vector-outlined page has no usable text layer no matter what its class
        # suggests, and must be rasterised at high DPI before OCR (Risk R11).
        if text_layer == TextLayer.VECTOR_OUTLINED and route == OCRRoute.NATIVE_TEXT:
            route = OCRRoute.TEXTRACT_TEXT
            reason = "text is vector-outlined; the native layer is empty and would read as blank"

        return RouteDecision(route, reason, self.cost_for(route))


def _resolve_path(configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else (_BACKEND_ROOT / path)


@functools.lru_cache(maxsize=4)
def load_routing_table(path: str | None = None) -> RoutingTable:
    """
    Load and cache the routing table.

    Cached per path for the process lifetime: the table is deployment
    configuration, so re-reading it mid-run would mean two pages of the same
    document were routed under different rules.
    """
    configured = path or get_settings().ocr_route_config
    resolved = _resolve_path(configured)
    if not resolved.exists():
        raise FileNotFoundError(
            f"OCR routing table not found at {resolved}. OCR_ROUTE_CONFIG must point at "
            f"a real file — §4.4 requires routing to be configuration, and there is no "
            f"hardcoded fallback table to fall back to."
        )
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    table = RoutingTable(raw, source=str(resolved))
    log.info(
        "routing table loaded", extra={"source": table.source, "version": table.version,
                                       "hash": table.content_hash}
    )
    return table


# ---------------------------------------------------------------------------
# Anchor matching (§4.3 Tier 3)
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalise_for_anchor(text: str) -> str:
    """
    Collapse text so anchors match regardless of spacing or punctuation.

    Anchor matching must be whitespace- and case-insensitive and tolerate
    letter-spaced titles: ``D O O R   S C H E D U L E`` is common in CAD title
    text and a naive ``"DOOR SCHEDULE" in page_text`` misses every one of them.

    Removing *all* non-alphanumerics turns both ``DOOR SCHEDULE`` and
    ``D O O R  S C H E D U L E`` into ``DOORSCHEDULE``.
    """
    return _NON_ALNUM.sub("", text.upper())
