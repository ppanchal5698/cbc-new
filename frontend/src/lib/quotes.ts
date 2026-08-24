"use client";

/**
 * Quotes, quote lines, and the vendor-RFQ loop (FR-5 … FR-16).
 *
 * Two rules govern everything here and both come straight from §6.2:
 *
 *  - **Only quantity, our_cost and margin_pct are human-entered** (§1.5). The
 *    write serializer accepts more, but sell, extended and the subtotals are
 *    derived, and offering to edit a derived figure would invite an estimator to
 *    disagree with the engine.
 *  - **Derived money is read, never recomputed here.** The API persists
 *    `sale_each`, `extended` and `subtotal` precisely so a quote reproduces
 *    identically months later; a browser that re-sums them would let the screen
 *    drift from the record it claims to show.
 *
 * So every mutation invalidates the quote and re-renders whatever the API
 * returned. There is no optimistic arithmetic anywhere in this file.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type { HardwareSetComponent, Quote, QuoteLine, VendorRFQ } from "./schema";

export const quoteKey = (projectId: string | undefined) => ["quotes", projectId] as const;

/**
 * The project's working quote.
 *
 * The pipeline creates exactly one DRAFT per project when matching finishes, so
 * the newest quote is the one an estimator means. An APPROVED or EXPORTED quote
 * still resolves here — stage 4 has to render what was actually sent.
 */
export function useQuote(projectId: string | undefined, options: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: quoteKey(projectId),
    queryFn: () => apiFetch<Paginated<Quote>>(`/api/quotes/?project=${projectId}&ordering=-created_at`),
    select: (page) => page.results[0] ?? null,
    enabled: Boolean(projectId),
    // The PDF render is enqueued rather than done on the request thread
    // (bottleneck B14), so the answer arrives on a later read. Without following
    // it, an estimator clicks Send and watches nothing happen — including when
    // the render failed.
    refetchInterval: options.poll ? 2500 : false,
  });
}

/** Invalidate everything a money change can move. */
function useQuoteInvalidator(projectId: string | undefined) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: quoteKey(projectId) });
    // The board shows quoted value per bid, so it moves whenever a line does.
    qc.invalidateQueries({ queryKey: ["projects"] });
  };
}

/**
 * Build the lines from the project's matched openings (FR-7).
 *
 * `replace` is the regenerate action. The API rebuilds generated lines and keeps
 * hand-added ones, and refuses outright without the flag — so the UI must ask
 * before passing it rather than retrying a 409 quietly.
 */
export function useGenerateLines(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<Quote, Error, { quoteId: string; replace?: boolean }>({
    mutationFn: ({ quoteId, replace }) =>
      apiFetch<Quote>(`/api/quotes/${quoteId}/generate-lines/${replace ? "?replace=true" : ""}`, {
        method: "POST",
      }),
    onSuccess: invalidate,
  });
}

/** The three editable fields, plus the override reason the database insists on. */
export interface LinePatch {
  quantity?: string;
  our_cost?: string;
  margin_pct?: string;
  margin_overridden?: boolean;
  margin_override_reason?: string;
  adders?: Record<string, string>;
  description?: string;
  needs_review?: boolean;
}

export function useUpdateLine(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<QuoteLine, Error, { id: string; patch: LinePatch }>({
    mutationFn: ({ id, patch }) =>
      apiFetch<QuoteLine>(`/api/quote-lines/${id}/`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: invalidate,
  });
}

export function useCreateLine(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<QuoteLine, Error, Partial<QuoteLine> & { quote: string }>({
    mutationFn: (body) =>
      apiFetch<QuoteLine>("/api/quote-lines/", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: invalidate,
  });
}

export function useDeleteLine(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiFetch<void>(`/api/quote-lines/${id}/`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

/** Freight and the tax jurisdiction live on the quote, not on a line. */
export function useUpdateQuote(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<Quote, Error, { id: string; patch: Partial<Quote> }>({
    mutationFn: ({ id, patch }) =>
      apiFetch<Quote>(`/api/quotes/${id}/`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: invalidate,
  });
}

export function useRecalculate(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<Quote, Error, string>({
    mutationFn: (id) => apiFetch<Quote>(`/api/quotes/${id}/recalculate/`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

/**
 * The NFR-1 gate.
 *
 * The only transition to APPROVED, and the only thing standing between a draft
 * and a customer-facing document. The API refuses while any line is still
 * flagged; the screen must make that visible *before* the click, not surface it
 * as an error afterwards.
 */
export function useApproveQuote(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<Quote, Error, { id: string; notes?: string }>({
    mutationFn: ({ id, notes }) =>
      apiFetch<Quote>(`/api/quotes/${id}/approve/`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, notes: notes ?? "" }),
      }),
    onSuccess: invalidate,
  });
}

/** Enqueues the WeasyPrint render (B14). The PDF appears on a later poll. */
export function useExportQuote(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  return useMutation<Quote, Error, string>({
    mutationFn: (id) => apiFetch<Quote>(`/api/quotes/${id}/export/`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

/** FR-11 — the closest prior quote, by brand, architect or GC. */
export function usePriorQuotes(keys: { brand?: string | null; architect?: string | null; gc?: string | null }) {
  const params = new URLSearchParams();
  if (keys.brand) params.set("brand", keys.brand);
  if (keys.architect) params.set("architect", keys.architect);
  if (keys.gc) params.set("gc", keys.gc);
  const query = params.toString();

  return useQuery({
    queryKey: ["quotes", "prior", query],
    queryFn: () => apiFetch<Paginated<Quote>>(`/api/quotes/search/?${query}`),
    select: (page) => page.results,
    enabled: query.length > 0,
  });
}

// ---------------------------------------------------------------------------
// Vendor RFQ loop (FR-16)
// ---------------------------------------------------------------------------

export function useVendorRfqs(quoteId: string | undefined) {
  return useQuery({
    queryKey: ["vendor-rfqs", quoteId],
    queryFn: () => apiFetch<Paginated<VendorRFQ>>(`/api/vendor-rfqs/?quote_line__quote=${quoteId}`),
    select: (page) => page.results,
    enabled: Boolean(quoteId),
  });
}

export function useRequestRfq(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  const qc = useQueryClient();
  return useMutation<VendorRFQ, Error, { quote_line: string; vendor: string; request_notes?: string }>({
    mutationFn: (body) =>
      apiFetch<VendorRFQ>("/api/vendor-rfqs/", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["vendor-rfqs"] });
    },
  });
}

/** Slot a returned vendor price into its draft line. The API re-prices the quote. */
export function useRecordRfqPrice(projectId: string | undefined) {
  const invalidate = useQuoteInvalidator(projectId);
  const qc = useQueryClient();
  return useMutation<VendorRFQ, Error, { id: string; returned_price: string }>({
    mutationFn: ({ id, returned_price }) =>
      apiFetch<VendorRFQ>(`/api/vendor-rfqs/${id}/record-price/`, {
        method: "POST",
        body: JSON.stringify({ returned_price }),
      }),
    onSuccess: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["vendor-rfqs"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Hardware sets (§5.11)
// ---------------------------------------------------------------------------

/**
 * Resolved and unresolved hardware-set callouts for a project.
 *
 * The unresolved ones matter most. The backend refuses to fill in what an `HW-3`
 * usually contains, so a callout the bid set never defines comes back
 * `resolved: false` — and hiding that would turn a stated refusal into a silent
 * omission, which is the one thing NFR-2 is about.
 */
export function useHardwareComponents(projectId: string | undefined, resolved?: boolean) {
  const filter = resolved === undefined ? "" : `&resolved=${resolved}`;
  return useQuery({
    queryKey: ["hardware-components", projectId, resolved],
    queryFn: () =>
      apiFetch<Paginated<HardwareSetComponent>>(
        `/api/hardware-components/?project=${projectId}&page_size=200${filter}`,
      ),
    select: (page) => page.results,
    enabled: Boolean(projectId),
  });
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

/** How a line's cost was sourced, in an estimator's words rather than an enum's. */
export function costSourceLabel(source: string | undefined): string {
  switch (source) {
    case "P21_LAST_PO":
      return "Last PO";
    case "DISTRIBUTOR_SHEET":
      return "Distributor sheet";
    case "MFR_LIST":
      return "List × multiplier";
    case "VENDOR_RFQ":
      return "Vendor quote";
    case "MANUAL":
      return "Entered by hand";
    default:
      return source ?? "—";
  }
}

export interface LineFlag {
  key: string;
  label: string;
  fg: string;
  bg: string;
  title: string;
}

/**
 * The badges beside a line, in the order they matter.
 *
 * `cost_is_stale` renders as the prototype's "Book lapsed". It is deliberately a
 * prompt and not an action: NR-2 asks for "price may be out of date — refresh",
 * and NFR-10 forbids refreshing underneath an estimator who did not ask.
 */
export function lineFlags(line: QuoteLine): LineFlag[] {
  const flags: LineFlag[] = [];

  if (line.needs_review) {
    // `needs_review` covers two different situations and the estimator has to be
    // able to tell them apart. A line with a real cost that says "needs a price"
    // is a badge people learn to ignore, which then hides the lines that do.
    const priced = Boolean(line.catalog_item) && Number(line.our_cost ?? 0) > 0;
    flags.push(
      priced
        ? {
            key: "list",
            label: "Undiscounted",
            fg: "var(--app-warn)",
            bg: "rgba(251,191,36,0.14)",
            title:
              "Priced at manufacturer list because no negotiated multiplier is on file for this vendor. It is a real cost basis, just not CBC's tier — check the price books.",
          }
        : {
            key: "review",
            label: "Needs a price",
            fg: "var(--app-warn)",
            bg: "rgba(251,191,36,0.14)",
            title:
              "Nothing in the catalogue satisfied this opening's hard constraints, or the best match scored below the cut-off. Price it yourself or pick a match.",
          },
    );
  }
  if (line.cost_is_stale) {
    flags.push({
      key: "stale",
      label: "Book lapsed",
      fg: "var(--app-warn)",
      bg: "rgba(251,191,36,0.14)",
      title: "This cost is older than the freshness window. Price may be out of date — refresh it.",
    });
  }
  if (line.below_floor_flag) {
    flags.push({
      key: "floor",
      label: "Below floor",
      fg: "var(--app-neg)",
      bg: "var(--app-neg-soft)",
      title: "Margin is below this product band's floor. Flagged, not blocked — approval routing is out of scope.",
    });
  }
  if (line.is_direct_equal) {
    flags.push({
      key: "equal",
      label: "Direct equal",
      fg: "var(--app-accent)",
      bg: "var(--app-accent-soft)",
      title: line.substitution_note || "Proposed as a direct equal. Choosing one is your call, not the system's.",
    });
  }
  return flags;
}

/**
 * CSI sections, in the order they appear on a CBC proposal.
 *
 * The quote groups by section rather than by the internal line group, because
 * that is how the trade reads a bid: Division 08 openings, Division 10
 * specialties, Division 06 finishes. Freight is not a section and sits last on
 * its own.
 */
export const DIVISION_LABELS: Record<string, string> = {
  "06 64 00": "Plastic panelling",
  "08 11 00": "Metal doors and frames",
  "08 14 00": "Wood doors",
  "08 71 00": "Door hardware",
  "10 28 00": "Toilet accessories",
};

export const divisionLabel = (division: string): string =>
  DIVISION_LABELS[division] ?? (division ? `Division ${division.slice(0, 2)}` : "Unsectioned");

export interface LineGroup {
  key: string;
  name: string;
  div: string;
  lines: QuoteLine[];
  /** Read off the line, never summed here (§6.2 step 5). */
  subtotal: string;
}

/**
 * Split a quote's lines into the blocks the sheet shows, in `line_order`.
 *
 * Freight keeps its own block regardless of section — it is a line with a
 * nullable amount rather than something priced under a programme (C11).
 *
 * The subtotal is read off the first member rather than summed: the pricing
 * engine stores it precisely so a quote reproduces months later, and a browser
 * that re-added the rows would let the screen drift from the record.
 */
export function groupLines(quote: Quote | null | undefined): LineGroup[] {
  const lines = [...(quote?.lines ?? [])].sort((a, b) => (a.line_order ?? 0) - (b.line_order ?? 0));

  const groups: LineGroup[] = [];
  const seen = new Set<string>();

  for (const line of lines) {
    if (line.line_group === "FREIGHT") continue;
    const key = line.csi_division || "";
    if (seen.has(key)) continue;
    seen.add(key);
    const members = lines.filter(
      (l) => l.line_group !== "FREIGHT" && (l.csi_division || "") === key,
    );
    groups.push({
      key: key || "none",
      name: divisionLabel(key),
      div: key || "—",
      lines: members,
      subtotal: members[0].subtotal,
    });
  }

  const freight = lines.filter((l) => l.line_group === "FREIGHT");
  if (freight.length) {
    groups.push({
      key: "FREIGHT",
      name: "Freight",
      div: "—",
      lines: freight,
      subtotal: freight[0].subtotal,
    });
  }
  return groups;
}

/**
 * The lines an estimator has changed by hand.
 *
 * Worth being able to isolate: on a forty-line quote the overrides are the ones
 * carrying a reason and an owner, and they are what a reviewer looks at first.
 */
export const editedLines = (quote: Quote | null | undefined): QuoteLine[] =>
  (quote?.lines ?? []).filter((l) => l.margin_overridden || l.cost_source === "MANUAL");

/** A number the API sent as a decimal string. `null` stays null, not zero. */
export const num = (v: string | number | null | undefined): number =>
  v === null || v === undefined || v === "" ? 0 : Number(v);
