"use client";

/**
 * Stage 3 · Quote — ported from the "stage 3 · quote" section of the Ops-Hub
 * prototype: the grouped grid, the per-group subtotals, the freight box and the
 * totals strip.
 *
 * Three adaptations, all forced by the real pricing engine:
 *
 *  - **Sell is not an input.** The prototype offers one. §1.5 is explicit that
 *    only quantity, cost and margin are human-entered and everything else
 *    derives, so an editable sell price would be a fourth human-entered field
 *    that the engine has no way to honour. It renders as the figure the API
 *    computed.
 *  - **Subtotals are read, not summed.** `subtotal` is a stored column so a
 *    quote reproduces months later (§6.2 step 5). Summing the rows on screen
 *    would let the display drift from the record the moment either changed.
 *  - **Hardware indents under its door.** The prototype's groups are flat; a real
 *    CBC quote is grouped by opening with that opening's hardware set beneath it,
 *    which is what `line_order` already encodes.
 */

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { EstimateShell } from "@/components/estimate/EstimateShell";
import { LineDetail } from "@/components/quote/LineDetail";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { ApiError } from "@/lib/api";
import { money0, money2, plural } from "@/lib/format";
import {
  costSourceLabel,
  groupLines,
  lineFlags,
  num,
  useApproveQuote,
  useDeleteLine,
  useGenerateLines,
  useHardwareComponents,
  useQuote,
  useUpdateLine,
  useUpdateQuote,
} from "@/lib/quotes";
import { useProject } from "@/lib/projects";
import type { Quote, QuoteLine } from "@/lib/schema";

const GRID = "170px minmax(140px,1fr) 62px 92px 92px 64px 118px 96px 28px";

export default function QuotePage() {
  return (
    <RequireAuth>
      <QuoteScreen />
    </RequireAuth>
  );
}

function QuoteScreen() {
  const { id } = useParams<{ id: string }>();
  const { data: project } = useProject(id);
  const { data: quote, isPending } = useQuote(id);
  const { data: unresolved } = useHardwareComponents(id, false);

  const generate = useGenerateLines(id);
  const approve = useApproveQuote(id);
  const updateQuote = useUpdateQuote(id);

  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const groups = useMemo(() => groupLines(quote), [quote]);
  const lines = quote?.lines ?? [];
  const blocking = lines.filter((l) => l.needs_review);

  function onGenerate(replace: boolean) {
    if (!quote) return;
    if (
      replace &&
      !window.confirm(
        "Rebuild the generated lines from the current matches?\n\n" +
          "Lines you added by hand are kept. Edits to generated lines are not.",
      )
    )
      return;
    setError(null);
    generate.mutate(
      { quoteId: quote.id, replace },
      { onError: (err) => setError(err instanceof ApiError ? err.message : String(err)) },
    );
  }

  function onApprove() {
    if (!quote) return;
    setError(null);
    approve.mutate(
      { id: quote.id },
      { onError: (err) => setError(err instanceof ApiError ? err.message : String(err)) },
    );
  }

  const selectedLine = lines.find((l) => l.id === selected) ?? null;

  return (
    <EstimateShell
      project={project}
      stage={3}
      subs={{
        1: "—",
        2: "—",
        3: quote ? (lines.length ? `${plural(lines.length, "line")} · ${money0(quote.grand_total)}` : "No lines yet") : "No quote",
        4: quote?.status === "APPROVED" || quote?.status === "EXPORTED" ? "Ready" : "—",
      }}
      hint={
        blocking.length
          ? `${plural(blocking.length, "line")} still flagged — a missing price, or one taken at undiscounted list. Approval is held until they are cleared.`
          : lines.length
            ? "Every figure is editable. Approve when the numbers are yours."
            : "Generate the lines from what was matched, or add them by hand."
      }
    >
      <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "grid", gridTemplateColumns: selected ? "minmax(0,1fr) 340px" : "minmax(0,1fr)", gap: "14px", padding: "14px 16px", overflow: "hidden" }}>
        <div style={{ minWidth: "0", display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)" }}>
          <div style={{ flexShrink: "0", display: "flex", alignItems: "center", gap: "12px", padding: "15px 18px 13px", borderBottom: "1px solid var(--app-line)" }}>
            <span style={{ display: "grid", placeItems: "center", width: "36px", height: "36px", borderRadius: "11px", background: "rgba(167,139,250,0.18)" }}>
              <i className="ph-duotone ph-calculator" style={{ fontSize: "19px", color: "#a78bfa" }}></i>
            </span>
            <span style={{ flex: "1", minWidth: "0" }}>
              <span style={{ display: "block", fontSize: "16px", fontWeight: "700", letterSpacing: "-0.01em" }}>Quote</span>
              <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "1px" }}>
                {quote
                  ? `${plural(lines.length, "line")} · every figure below is editable`
                  : isPending
                    ? "Loading…"
                    : "Nothing priced yet"}
              </span>
            </span>

            <TaxPicker
              value={quote?.tax_jurisdiction ?? null}
              disabled={!quote || quote.status !== "DRAFT" || updateQuote.isPending}
              onPick={(jurisdiction) =>
                quote && updateQuote.mutate({ id: quote.id, patch: { tax_jurisdiction: jurisdiction } })
              }
            />

            {quote && quote.status === "DRAFT" ? (
              <button
                onClick={() => onGenerate(lines.length > 0)}
                disabled={generate.isPending}
                className="hv-f68886"
                style={{ display: "flex", alignItems: "center", gap: "7px", background: "var(--app-panel-2)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", borderRadius: "10px", padding: "8px 12px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: "600", cursor: generate.isPending ? "progress" : "pointer", whiteSpace: "nowrap" }}
              >
                <i className={lines.length ? "ph-duotone ph-arrows-clockwise" : "ph-duotone ph-plus"} style={{ fontSize: "15px" }}></i>
                {generate.isPending ? "Working…" : lines.length ? "Rebuild lines" : "Generate lines"}
              </button>
            ) : null}
          </div>

          {error ? (
            <div role="alert" style={{ flexShrink: 0, margin: "12px 18px 0", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "10px 12px", fontSize: "12.5px", lineHeight: 1.55 }}>
              {error}
            </div>
          ) : null}

          {unresolved?.length ? <UnresolvedBanner groups={unresolved.map((c) => c.hardware_group)} /> : null}

          <div style={{ flexShrink: "0", display: "grid", gridTemplateColumns: GRID, gap: "0 8px", padding: "9px 18px", borderBottom: "1px solid var(--app-line)", fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
            <span>Part</span>
            <span>Description</span>
            <span>Qty</span>
            <span style={{ textAlign: "right" }}>Cost</span>
            <span style={{ textAlign: "right" }}>Sell</span>
            <span style={{ textAlign: "right" }}>Margin</span>
            <span>Basis</span>
            <span style={{ textAlign: "right" }}>Extended</span>
            <span></span>
          </div>

          <div style={{ flex: "1", minHeight: "0", overflowY: "auto", overflowX: "hidden", padding: "6px 18px 18px" }}>
            {!quote && !isPending ? (
              <Empty />
            ) : (
              groups.map((g) => (
                <div key={g.key} style={{ marginBottom: "14px" }}>
                  <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "12px", padding: "8px 2px", borderBottom: "1px solid var(--app-line)" }}>
                    <span style={{ display: "flex", alignItems: "baseline", gap: "9px" }}>
                      <span style={{ fontSize: "13.5px", fontWeight: "700" }}>{g.name}</span>
                      <span style={{ fontSize: "11px", color: "var(--app-tx-3)" }}>
                        {g.div} · {plural(g.lines.length, "line")}
                      </span>
                    </span>
                    <span style={{ fontSize: "14px", fontWeight: "700" }} title="Stored by the pricing engine, not summed in the browser.">
                      {g.key === "FREIGHT" ? quote?.freight_display === "TBD" ? "TBD" : money2(g.subtotal) : money2(g.subtotal)}
                    </span>
                  </div>
                  {g.lines.map((line) => (
                    <Row
                      key={line.id}
                      line={line}
                      projectId={id}
                      locked={quote?.status !== "DRAFT"}
                      selected={selected === line.id}
                      onSelect={() => setSelected(selected === line.id ? null : line.id)}
                    />
                  ))}
                </div>
              ))
            )}
          </div>

          <Totals
            quote={quote ?? null}
            blocking={blocking}
            approving={approve.isPending}
            onApprove={onApprove}
            onFreight={(value) =>
              quote &&
              updateQuote.mutate({
                id: quote.id,
                // An empty box is not zero freight — it is the TBD the estimator
                // has not filled in, and C11 wants that absence preserved.
                patch: { freight_amount: value.trim() === "" ? null : value },
              })
            }
          />
        </div>

        {selectedLine ? (
          <LineDetail
            line={selectedLine}
            projectId={id}
            locked={quote?.status !== "DRAFT"}
            onClose={() => setSelected(null)}
          />
        ) : null}
      </div>
    </EstimateShell>
  );
}

/* ------------------------------------------------------------------ row --- */

function Row({
  line,
  projectId,
  locked,
  selected,
  onSelect,
}: {
  line: QuoteLine;
  projectId: string;
  locked: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const update = useUpdateLine(projectId);
  const remove = useDeleteLine(projectId);
  const flags = lineFlags(line);
  const hardware = Boolean(line.hardware_component);

  const bar = line.needs_review
    ? "var(--app-warn)"
    : line.below_floor_flag
      ? "var(--app-neg)"
      : selected
        ? "var(--app-accent)"
        : "transparent";

  return (
    <div
      onClick={onSelect}
      className="hv-40d530"
      style={{ display: "grid", gridTemplateColumns: GRID, gap: "0 8px", alignItems: "center", padding: "7px 0", borderBottom: "1px solid var(--app-line)", borderLeft: `2px solid ${bar}`, borderRadius: "8px", background: selected ? "var(--app-accent-soft)" : undefined, cursor: "pointer", transition: "background 150ms ease" }}
    >
      <span style={{ fontSize: "11.5px", fontWeight: "600", paddingLeft: hardware ? "20px" : "6px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", color: hardware ? "var(--app-tx-2)" : "var(--app-tx)" }} title={hardware ? "Part of this opening's hardware set" : undefined}>
        {hardware ? "↳ " : ""}
        {line.catalog_item_detail?.sku || "—"}
      </span>
      <span style={{ minWidth: "0", fontSize: "12.5px", color: "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={line.description}>
        {line.description || "—"}
      </span>

      <Cell value={line.quantity} locked={locked} align="right" onCommit={(v) => update.mutate({ id: line.id, patch: { quantity: v } })} />
      <Cell value={line.our_cost} locked={locked} align="right" onCommit={(v) => update.mutate({ id: line.id, patch: { our_cost: v } })} />

      {/* Derived. See the module note — sell is not a human-entered field. */}
      <span style={{ fontSize: "12.5px", fontWeight: 600, textAlign: "right", fontVariantNumeric: "tabular-nums" }} title="cost ÷ (1 − margin), computed and stored by the pricing engine">
        {money2(line.sale_each)}
      </span>

      <MarginCell line={line} projectId={projectId} locked={locked} />

      <span style={{ justifySelf: "start", display: "flex", alignItems: "center", gap: "5px", minWidth: "0", overflow: "hidden" }}>
        {flags.length ? (
          flags.slice(0, 1).map((f) => (
            <span key={f.key} title={f.title} style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "10.5px", fontWeight: "600", color: f.fg, background: f.bg, borderRadius: "7px", padding: "3px 7px", whiteSpace: "nowrap" }}>
              {f.key === "stale" ? <i className="ph-duotone ph-warning" style={{ fontSize: "13px" }}></i> : null}
              {f.label}
            </span>
          ))
        ) : (
          <span style={{ fontSize: "10.5px", color: "var(--app-tx-3)", whiteSpace: "nowrap" }} title={line.cost_effective_date ? `Effective ${line.cost_effective_date}` : undefined}>
            {costSourceLabel(line.cost_source)}
          </span>
        )}
      </span>

      <span style={{ fontSize: "13px", fontWeight: "700", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{money2(line.extended)}</span>

      <button
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm(`Remove "${line.description || "this line"}" from the quote?`)) remove.mutate(line.id);
        }}
        disabled={locked || remove.isPending}
        title={locked ? "This quote is no longer a draft." : "Remove this line"}
        className="hv-78b3f3"
        style={{ display: "grid", placeItems: "center", width: "26px", height: "26px", borderRadius: "7px", background: "transparent", border: "0", color: "var(--app-tx-3)", cursor: locked ? "not-allowed" : "pointer" }}
      >
        <i className="ph-duotone ph-trash" style={{ fontSize: "14px" }}></i>
      </button>
    </div>
  );
}

/** An editable decimal that only writes when it actually changed. */
function Cell({
  value,
  locked,
  align,
  onCommit,
}: {
  value: string | undefined;
  locked: boolean;
  align: "left" | "right";
  onCommit: (v: string) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value ?? "");

  return (
    <input
      value={shown}
      disabled={locked}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== null && draft !== value) onCommit(draft);
        setDraft(null);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(null);
      }}
      style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "8px", padding: "6px 9px", fontFamily: "var(--app-font)", fontSize: "12.5px", color: "var(--app-tx)", outline: "none", width: "100%", textAlign: align, fontVariantNumeric: "tabular-nums", opacity: locked ? 0.6 : 1 }}
    />
  );
}

/**
 * Margin, with the reason the database insists on.
 *
 * `ck_override_requires_reason` refuses an override with an empty reason, so the
 * UI asks for one rather than letting the estimator discover a 400. An
 * unexplained margin change on a customer-facing document is an audit failure.
 */
function MarginCell({ line, projectId, locked }: { line: QuoteLine; projectId: string; locked: boolean }) {
  const update = useUpdateLine(projectId);
  const [draft, setDraft] = useState<string | null>(null);

  function commit() {
    if (draft === null || draft === line.margin_pct) return setDraft(null);
    const reason = window.prompt(
      "Why is this line's margin being overridden?\n\nIt goes on the audit trail beside the number.",
      line.margin_override_reason || "",
    );
    if (reason === null || !reason.trim()) return setDraft(null);
    update.mutate({
      id: line.id,
      patch: { margin_pct: draft, margin_overridden: true, margin_override_reason: reason.trim() },
    });
    setDraft(null);
  }

  return (
    <input
      value={draft ?? (line.margin_pct ?? "")}
      disabled={locked}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(null);
      }}
      title={line.margin_overridden ? `Overridden — ${line.margin_override_reason}` : "Band default"}
      style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "8px", padding: "6px 9px", fontFamily: "var(--app-font)", fontSize: "12.5px", color: line.below_floor_flag ? "var(--app-neg)" : line.margin_overridden ? "var(--app-accent)" : "var(--app-tx)", outline: "none", width: "100%", textAlign: "right", fontWeight: 600, fontVariantNumeric: "tabular-nums", opacity: locked ? 0.6 : 1 }}
    />
  );
}

/* --------------------------------------------------------------- totals --- */

function Totals({
  quote,
  blocking,
  approving,
  onApprove,
  onFreight,
}: {
  quote: Quote | null;
  blocking: QuoteLine[];
  approving: boolean;
  onApprove: () => void;
  onFreight: (v: string) => void;
}) {
  const [freight, setFreight] = useState<string | null>(null);
  const locked = !quote || quote.status !== "DRAFT";

  const totals = [
    { label: "Sub-total", val: money2(quote?.subtotal_sale), size: "17px", fg: "var(--app-tx)" },
    {
      label: quote?.tax_jurisdiction ? `Tax · ${quote.tax_jurisdiction}` : "Tax",
      val: quote?.tax_jurisdiction ? money2(quote.tax_amount) : "None",
      size: "17px",
      fg: quote?.tax_jurisdiction ? "var(--app-tx)" : "var(--app-tx-3)",
    },
    { label: "Grand total", val: money2(quote?.grand_total), size: "22px", fg: "var(--app-accent)" },
  ];

  return (
    <div style={{ flexShrink: "0", display: "grid", gridTemplateColumns: "minmax(0,1fr) 150px repeat(3,130px) 150px", gap: "0 18px", alignItems: "end", padding: "14px 18px 16px", borderTop: "1px solid var(--app-line)", background: "var(--app-bg-2)", borderRadius: "0 0 16px 16px" }}>
      <div style={{ minWidth: "0", fontSize: "11.5px", color: "var(--app-tx-3)", lineHeight: "1.55" }}>
        Supply-only material; Hamilton Parker PO required; valid 30 days. Tax applies in Ohio and
        Kentucky only. Freight is quoted separately unless you enter it.
      </div>

      <div>
        <div style={{ fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
          Freight &amp; pallet
        </div>
        <input
          value={freight ?? (quote?.freight_amount ?? "")}
          placeholder="TBD"
          disabled={locked}
          onChange={(e) => setFreight(e.target.value)}
          onBlur={() => {
            if (freight !== null && freight !== (quote?.freight_amount ?? "")) onFreight(freight);
            setFreight(null);
          }}
          style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "8px", padding: "6px 9px", fontFamily: "var(--app-font)", fontSize: "12.5px", color: "var(--app-tx)", outline: "none", width: "100%", marginTop: "5px", fontVariantNumeric: "tabular-nums" }}
        />
      </div>

      {totals.map((t) => (
        <div key={t.label} style={{ textAlign: "right" }}>
          <div style={{ fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>{t.label}</div>
          <div style={{ fontSize: t.size, fontWeight: "800", letterSpacing: "-0.02em", color: t.fg, marginTop: "3px", fontVariantNumeric: "tabular-nums" }}>{t.val}</div>
        </div>
      ))}

      <ApproveButton quote={quote} blocking={blocking} approving={approving} onApprove={onApprove} />
    </div>
  );
}

/**
 * The NFR-1 hard gate.
 *
 * Held while any line still needs a price, and it says which — a disabled button
 * that will not explain itself is the thing an estimator learns to distrust.
 */
function ApproveButton({
  quote,
  blocking,
  approving,
  onApprove,
}: {
  quote: Quote | null;
  blocking: QuoteLine[];
  approving: boolean;
  onApprove: () => void;
}) {
  if (!quote) return <span />;

  if (quote.status !== "DRAFT") {
    return (
      <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", background: "var(--app-accent-soft)", border: "1px solid var(--app-accent-line)", color: "var(--app-accent)", borderRadius: "11px", padding: "11px", fontSize: "13px", fontWeight: "700", whiteSpace: "nowrap" }}>
        <i className="ph-duotone ph-seal-check" style={{ fontSize: "17px" }}></i>
        {quote.status === "EXPORTED" ? "Sent" : "Approved"}
      </span>
    );
  }

  const held = blocking.length > 0 || !quote.lines.length;
  // The API refuses while any line is flagged, whatever the reason — a missing
  // price and an undiscounted list price both count. Saying which lines and why
  // is the difference between a gate and a wall.
  const why = !quote.lines.length
    ? "There is nothing to approve yet."
    : `Held on: ${blocking
        .slice(0, 4)
        .map((l) => l.description || "an unnamed line")
        .join("; ")}${blocking.length > 4 ? `; and ${blocking.length - 4} more` : ""}.`;

  return (
    <button
      onClick={onApprove}
      disabled={held || approving}
      title={held ? why : "Approve this quote. Nothing is sent until you do."}
      style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", background: held ? "var(--app-panel-2)" : "linear-gradient(135deg,#818cf8,#22d3ee)", color: held ? "var(--app-tx-3)" : "#0a0a12", border: held ? "1px solid var(--app-line)" : "0", borderRadius: "11px", padding: "11px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: "700", cursor: held ? "not-allowed" : approving ? "progress" : "pointer", whiteSpace: "nowrap" }}
    >
      <i className="ph-duotone ph-check-circle" style={{ fontSize: "17px" }}></i>
      {approving ? "Approving…" : held ? `${blocking.length} to check` : "Approve"}
    </button>
  );
}

/* ---------------------------------------------------------------- bits --- */

/** OH and KY only. Everywhere else is untaxed because CBC sells to the GC (§1.1). */
function TaxPicker({
  value,
  disabled,
  onPick,
}: {
  value: string | null;
  disabled: boolean;
  onPick: (v: string | null) => void;
}) {
  const options: [string, string | null][] = [
    ["No tax", null],
    ["OH", "OH"],
    ["KY", "KY"],
  ];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "5px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "10px", padding: "4px" }} title="Sales tax applies only where CBC holds nexus.">
      {options.map(([label, v]) => {
        const on = value === v;
        return (
          <button
            key={label}
            onClick={() => onPick(v)}
            disabled={disabled}
            style={{ background: on ? "var(--app-tx)" : "transparent", border: "0", color: on ? "var(--app-bg-2)" : "var(--app-tx)", borderRadius: "7px", padding: "6px 10px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: "600", cursor: disabled ? "not-allowed" : "pointer", whiteSpace: "nowrap", opacity: disabled ? 0.5 : 1 }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Hardware callouts the bid set never defined (§5.11).
 *
 * The extraction refuses to supply what an HW-3 usually contains, so these
 * openings produce no hardware lines at all. That is a finding an estimator has
 * to act on, and burying it would turn a stated refusal into a silent omission.
 */
function UnresolvedBanner({ groups }: { groups: string[] }) {
  const unique = [...new Set(groups)];
  return (
    <div style={{ flexShrink: 0, margin: "12px 18px 0", display: "flex", alignItems: "flex-start", gap: "10px", background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)", borderRadius: "10px", padding: "10px 12px" }}>
      <i className="ph-duotone ph-warning" style={{ fontSize: "17px", color: "var(--app-warn)", flexShrink: 0, marginTop: "1px" }}></i>
      <span style={{ fontSize: "12.5px", color: "var(--app-tx-2)", lineHeight: 1.55 }}>
        <strong style={{ color: "var(--app-warn)" }}>{unique.join(", ")}</strong>{" "}
        {unique.length === 1 ? "is called out" : "are called out"} in the door schedule and this bid
        set never defines {unique.length === 1 ? "it" : "them"}. No hardware has been assumed — add
        those lines by hand, or upload the Division 08 spec section.
      </span>
    </div>
  );
}

function Empty() {
  return (
    <div style={{ display: "grid", placeItems: "center", padding: "64px 20px 40px", textAlign: "center" }}>
      <span style={{ display: "grid", placeItems: "center", width: "66px", height: "66px", borderRadius: "20px", background: "rgba(167,139,250,0.18)", marginBottom: "18px" }}>
        <i className="ph-duotone ph-calculator" style={{ fontSize: "32px", color: "#a78bfa" }}></i>
      </span>
      <span style={{ fontSize: "20px", fontWeight: "700", letterSpacing: "-0.015em" }}>No quote yet</span>
      <span style={{ fontSize: "13.5px", color: "var(--app-tx-2)", maxWidth: "420px", marginTop: "7px", lineHeight: "1.6" }}>
        A draft is built automatically once a bid set has been read and matched. If the documents
        are still being read, this fills itself in.
      </span>
    </div>
  );
}

export { num };
