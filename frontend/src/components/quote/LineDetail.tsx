"use client";

/**
 * The right-hand panel of Stage 3: everything behind one quote line.
 *
 * Its job is to make a proposed line **interrogable**. §6.1 stores each hard
 * constraint's verdict separately for exactly this reason — a match an estimator
 * cannot question is a match they will not trust, and "scored low" is not an
 * answer to "why not this one?". So rating, handing and division each show their
 * own pass or fail rather than collapsing into a confidence number.
 *
 * Choosing a direct equal stays the estimator's call (§1.4). The system records
 * the substitution and the note; it never decides one.
 */

import { useState } from "react";
import { money2 } from "@/lib/format";
import { useMatches } from "@/lib/openings";
import { costSourceLabel, useRecordRfqPrice, useRequestRfq, useUpdateLine, useVendorRfqs } from "@/lib/quotes";
import { apiFetch } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import type { Match, QuoteLine } from "@/lib/schema";

export function LineDetail({
  line,
  projectId,
  locked,
  onClose,
}: {
  line: QuoteLine;
  projectId: string;
  locked: boolean;
  onClose: () => void;
}) {
  const { data: matches } = useMatches(line.opening ?? undefined);
  const { data: rfqs } = useVendorRfqs(line.quote);
  const lineRfq = rfqs?.find((r) => r.quote_line === line.id) ?? null;

  return (
    <div style={{ minWidth: "0", display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", animation: "fadein 220ms cubic-bezier(0.32,0.72,0,1)" }}>
      <div style={{ flexShrink: 0, display: "flex", alignItems: "flex-start", gap: "10px", padding: "14px 16px 12px", borderBottom: "1px solid var(--app-line)" }}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontSize: "14px", fontWeight: 700, letterSpacing: "-0.01em" }}>
            {line.catalog_item_detail?.sku || "Unpriced line"}
          </span>
          <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "2px", lineHeight: 1.5 }}>
            {line.description || "No description"}
          </span>
        </span>
        <button onClick={onClose} className="hv-114a69" style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "9px", background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx-3)", cursor: "pointer", flexShrink: 0 }}>
          <i className="ph-duotone ph-x" style={{ fontSize: "15px" }}></i>
        </button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden", padding: "14px 16px 20px" }}>
        <Section label="Where the cost came from" />
        <Facts
          rows={[
            ["Basis", costSourceLabel(line.cost_source)],
            ["Effective", line.cost_effective_date || "not dated"],
            ["List", line.list_price ? money2(line.list_price) : "—"],
            ["Multiplier", line.multiplier ?? "—"],
            // NFR-3 wants the sheet version and the tier, not just the number.
            ["Sheet", line.multiplier_sheet_version || "—"],
            ["P21", line.p21_reference || "not linked"],
          ]}
        />
        {line.cost_is_stale ? (
          <RefreshPrompt line={line} projectId={projectId} locked={locked} />
        ) : null}

        <Section label="Adders" hint="Outside the base price book — electrification, NRP hinges, premium finishes." />
        <Adders line={line} projectId={projectId} locked={locked} />

        {line.opening ? (
          <>
            <Section label="Candidates" hint="Hard constraints filter; the rest score. No model decides this." />
            <Matches matches={matches ?? []} line={line} projectId={projectId} locked={locked} />
          </>
        ) : null}

        <Section label="Vendor quote" hint="For large, custom or first-time items the book cannot price." />
        <Rfq line={line} rfq={lineRfq} projectId={projectId} locked={locked} />
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- pieces --- */

function Section({ label, hint }: { label: string; hint?: string }) {
  return (
    <div style={{ marginTop: "18px", marginBottom: "9px" }}>
      <div style={{ fontSize: "10.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>{label}</div>
      {hint ? <div style={{ fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "4px", lineHeight: 1.5 }}>{hint}</div> : null}
    </div>
  );
}

function Facts({ rows }: { rows: [string, string][] }) {
  return (
    <div>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "grid", gridTemplateColumns: "84px minmax(0,1fr)", gap: "10px", alignItems: "baseline", padding: "6px 0", borderBottom: "1px solid var(--app-line)" }}>
          <span style={{ fontSize: "12px", color: "var(--app-tx-3)" }}>{k}</span>
          <span style={{ fontSize: "12.5px", wordBreak: "break-word", fontVariantNumeric: "tabular-nums" }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * NR-2's "price may be out of date — refresh" prompt.
 *
 * An explicit action, never automatic. A price that moves underneath an estimator
 * without their knowledge is precisely the stale-data failure NFR-10 is about, so
 * re-sourcing happens when they ask and not a moment before.
 */
function RefreshPrompt({ line, projectId, locked }: { line: QuoteLine; projectId: string; locked: boolean }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setBusy(true);
    try {
      // Clearing the cost is what makes the engine run the waterfall again;
      // it will not re-source a line that already carries a figure.
      await apiFetch(`/api/quote-lines/${line.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ our_cost: "0" }),
      });
      qc.invalidateQueries({ queryKey: ["quotes", projectId] });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: "10px", background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)", borderRadius: "10px", padding: "10px 12px" }}>
      <div style={{ fontSize: "12px", color: "var(--app-tx-2)", lineHeight: 1.55 }}>
        This price may be out of date — it is older than the freshness window. Nothing has been
        changed for you.
      </div>
      <button
        onClick={refresh}
        disabled={locked || busy}
        style={{ marginTop: "9px", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-warn)", borderRadius: "8px", padding: "6px 11px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: locked ? "not-allowed" : busy ? "progress" : "pointer" }}
      >
        {busy ? "Re-sourcing…" : "Refresh this price"}
      </button>
    </div>
  );
}

/** Manual adders (NR-4), as editable key/value rows. */
function Adders({ line, projectId, locked }: { line: QuoteLine; projectId: string; locked: boolean }) {
  const update = useUpdateLine(projectId);
  const adders = (line.adders ?? {}) as Record<string, string>;
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState("");

  function commit(next: Record<string, string>) {
    update.mutate({ id: line.id, patch: { adders: next } });
  }

  return (
    <div>
      {Object.entries(adders).map(([k, v]) => (
        <div key={k} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 70px 24px", gap: "8px", alignItems: "center", padding: "6px 0", borderBottom: "1px solid var(--app-line)" }}>
          <span style={{ fontSize: "12.5px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{k}</span>
          <span style={{ fontSize: "12.5px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{money2(v)}</span>
          <button
            onClick={() => {
              const next = { ...adders };
              delete next[k];
              commit(next);
            }}
            disabled={locked}
            className="hv-78b3f3"
            style={{ display: "grid", placeItems: "center", background: "transparent", border: 0, color: "var(--app-tx-3)", cursor: locked ? "not-allowed" : "pointer" }}
          >
            <i className="ph-duotone ph-x" style={{ fontSize: "13px" }}></i>
          </button>
        </div>
      ))}

      {!Object.keys(adders).length ? (
        <div style={{ fontSize: "12px", color: "var(--app-tx-3)", padding: "4px 0" }}>None on this line.</div>
      ) : null}

      {!locked ? (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 70px 28px", gap: "8px", marginTop: "9px" }}>
          <input
            value={label}
            placeholder="Electrification"
            onChange={(e) => setLabel(e.target.value)}
            style={INPUT}
          />
          <input value={amount} placeholder="0.00" onChange={(e) => setAmount(e.target.value)} style={{ ...INPUT, textAlign: "right" }} />
          <button
            onClick={() => {
              if (!label.trim() || !amount.trim()) return;
              commit({ ...adders, [label.trim()]: amount.trim() });
              setLabel("");
              setAmount("");
            }}
            className="hv-f68886"
            style={{ display: "grid", placeItems: "center", background: "var(--app-panel-2)", border: "1px solid var(--app-line)", borderRadius: "8px", color: "var(--app-tx-2)", cursor: "pointer" }}
          >
            <i className="ph-duotone ph-plus" style={{ fontSize: "14px" }}></i>
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Ranked candidates, each explaining itself.
 *
 * A rejected match names the constraint that failed. That mirrors the estimator
 * behaviour CBC validated — *"here are 3 close matches, is it one of these?"* —
 * and it is the difference between a tool that proposes and one that decides.
 */
function Matches({
  matches,
  line,
  projectId,
  locked,
}: {
  matches: Match[];
  line: QuoteLine;
  projectId: string;
  locked: boolean;
}) {
  const qc = useQueryClient();
  const update = useUpdateLine(projectId);
  const [busy, setBusy] = useState<string | null>(null);

  // Only candidates for this line: the door line has no hardware component, a
  // hardware line has exactly one.
  const relevant = matches.filter((m) => (m.hardware_component ?? null) === (line.hardware_component ?? null));

  if (!relevant.length) {
    return (
      <div style={{ fontSize: "12px", color: "var(--app-tx-3)", lineHeight: 1.55 }}>
        Nothing in the catalogue satisfied this opening&rsquo;s hard constraints. That is the manual
        path, not a failure — price it yourself.
      </div>
    );
  }

  async function accept(match: Match) {
    setBusy(match.id);
    try {
      await apiFetch(`/api/matches/${match.id}/accept/`, { method: "POST" });
      // Point the line at what was just accepted; the API re-prices it.
      await update.mutateAsync({ id: line.id, patch: {} });
      await apiFetch(`/api/quote-lines/${line.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ match: match.id, catalog_item: match.catalog_item, our_cost: "0" }),
      });
      qc.invalidateQueries({ queryKey: ["quotes", projectId] });
      qc.invalidateQueries({ queryKey: ["matches"] });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ display: "grid", gap: "8px" }}>
      {relevant.map((m) => {
        const chosen = line.match === m.id;
        return (
          <div key={m.id} style={{ background: chosen ? "var(--app-accent-soft)" : "var(--app-bg-2)", border: `1px solid ${chosen ? "var(--app-accent-line)" : "var(--app-line)"}`, borderRadius: "11px", padding: "10px 11px" }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
              <span style={{ fontSize: "12.5px", fontWeight: 700, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {m.catalog_vendor} {m.catalog_sku}
              </span>
              <span style={{ fontSize: "11.5px", color: "var(--app-tx-3)", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
                #{m.rank} · {Math.round((m.match_confidence ?? 0) * 100)}%
              </span>
            </div>
            <div style={{ fontSize: "11.5px", color: "var(--app-tx-2)", marginTop: "3px", lineHeight: 1.5 }}>{m.catalog_description}</div>

            <div style={{ display: "flex", gap: "5px", marginTop: "8px", flexWrap: "wrap" }}>
              <Verdict ok={m.rating_ok} label="Rating" hard />
              <Verdict ok={m.handing_ok} label="Handing" hard />
              <Verdict ok={m.division_ok} label="Division" hard />
              <Verdict ok={m.finish_ok} label="Finish" />
            </div>

            {m.rejection_reason ? (
              <div style={{ fontSize: "11px", color: "var(--app-tx-3)", marginTop: "7px", lineHeight: 1.5 }}>{m.rejection_reason}</div>
            ) : null}

            {!locked && !chosen ? (
              <button
                onClick={() => accept(m)}
                disabled={busy === m.id}
                className="hv-f68886"
                style={{ marginTop: "9px", width: "100%", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx)", borderRadius: "8px", padding: "7px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: busy === m.id ? "progress" : "pointer" }}
              >
                {busy === m.id ? "Applying…" : "Use this one"}
              </button>
            ) : null}
            {chosen ? (
              <div style={{ marginTop: "8px", fontSize: "11.5px", color: "var(--app-accent)", fontWeight: 600 }}>On the quote</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function Verdict({ ok, label, hard }: { ok: boolean | undefined; label: string; hard?: boolean }) {
  const good = ok !== false;
  return (
    <span
      title={hard ? `${label} is a hard constraint — a failure disqualifies the candidate however well it scores.` : `${label} is scored, not filtered.`}
      style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "10.5px", fontWeight: 600, color: good ? "var(--app-pos)" : "var(--app-neg)", background: good ? "rgba(52,211,153,0.12)" : "var(--app-neg-soft)", borderRadius: "6px", padding: "2px 7px", whiteSpace: "nowrap" }}
    >
      <i className={good ? "ph-duotone ph-check" : "ph-duotone ph-x"} style={{ fontSize: "11px" }}></i>
      {label}
    </span>
  );
}

/** FR-16 — mark a line awaiting a vendor quote, then slot the price back in. */
function Rfq({
  line,
  rfq,
  projectId,
  locked,
}: {
  line: QuoteLine;
  rfq: { id: string; vendor: string; status?: string; returned_price?: string | null } | null;
  projectId: string;
  locked: boolean;
}) {
  const request = useRequestRfq(projectId);
  const record = useRecordRfqPrice(projectId);
  const [vendor, setVendor] = useState("");
  const [price, setPrice] = useState("");

  if (rfq && rfq.status !== "RETURNED") {
    return (
      <div style={{ background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "10px 11px" }}>
        <div style={{ fontSize: "12.5px", fontWeight: 600 }}>Awaiting {rfq.vendor}</div>
        <div style={{ fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "3px", lineHeight: 1.5 }}>
          Enter the price when it comes back and it drops onto this line.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "8px", marginTop: "9px" }}>
          <input value={price} placeholder="0.00" onChange={(e) => setPrice(e.target.value)} style={{ ...INPUT, textAlign: "right" }} />
          <button
            onClick={() => price.trim() && record.mutate({ id: rfq.id, returned_price: price.trim() })}
            disabled={locked || record.isPending}
            className="hv-f68886"
            style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx)", borderRadius: "8px", padding: "6px 12px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: "pointer" }}
          >
            Record
          </button>
        </div>
      </div>
    );
  }

  if (rfq) {
    return (
      <div style={{ fontSize: "12px", color: "var(--app-tx-2)", lineHeight: 1.55 }}>
        {rfq.vendor} quoted {money2(rfq.returned_price)} and it is on the line.
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "8px" }}>
      <input value={vendor} placeholder="Vendor to ask" onChange={(e) => setVendor(e.target.value)} style={INPUT} />
      <button
        onClick={() => vendor.trim() && request.mutate({ quote_line: line.id, vendor: vendor.trim() })}
        disabled={locked || request.isPending}
        className="hv-f68886"
        style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", borderRadius: "8px", padding: "6px 12px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: locked ? "not-allowed" : "pointer", whiteSpace: "nowrap" }}
      >
        Request
      </button>
    </div>
  );
}

const INPUT: React.CSSProperties = {
  background: "var(--app-panel)",
  border: "1px solid var(--app-line)",
  borderRadius: "8px",
  padding: "6px 9px",
  fontFamily: "var(--app-font)",
  fontSize: "12.5px",
  color: "var(--app-tx)",
  outline: "none",
  width: "100%",
};
