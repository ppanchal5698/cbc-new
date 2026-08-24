"use client";

/**
 * Stage 4 · Proposal — ported from the "stage 4 · proposal" section of the
 * Ops-Hub prototype, which carries CBC's real customer-facing layout: the
 * letterhead, the three contact blocks, ALL BIDDERS terms, the part table and
 * the totals stack.
 *
 * **Nothing on this page computes money.** Every figure is read from the stored
 * quote, because §6.2 step 5 persists `sale_each`, `extended` and `subtotal`
 * precisely so a proposal sent in March still shows March's numbers in
 * September. A document that recalculated on render would quietly disagree with
 * the record it claims to represent.
 *
 * ⚠ The exact styling is blocked on open item Q10 — CBC's actual quote workbook
 * has not been provided. The structure here is what §6.2 step 4 and FR-10
 * specify; swapping in the real layout is a template change, not a rewrite.
 */

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { EstimateShell } from "@/components/estimate/EstimateShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { ApiError } from "@/lib/api";
import { money2, plural } from "@/lib/format";
import { useProject } from "@/lib/projects";
import { GROUP_LABELS, groupLines, num, useExportQuote, useQuote, useUpdateLine } from "@/lib/quotes";
import type { Project, Quote } from "@/lib/schema";
import { useMe } from "@/lib/session";

const DOC = "150px 34px 30px minmax(0,1fr) 72px 78px";

const TERMS =
  "Supply-only material; installation labour is not included. Hamilton Parker Company purchase " +
  "order required. Quotation valid for 30 days from the date above. Sales tax applies only where " +
  "CBC holds nexus (Ohio and Kentucky). Freight shown as TBD is not included and will be quoted " +
  "separately.";

const EXCLUSIONS = [
  "Aluminium and glass storefront, coiling and overhead doors.",
  "Oversized and garage doors — a separate Hamilton Parker division.",
  "Ceiling tile and grid, tile, brick and masonry.",
  "Installation, field measurement and site labour of any kind.",
];

export default function ProposalPage() {
  return (
    <RequireAuth>
      <Proposal />
    </RequireAuth>
  );
}

function Proposal() {
  const { id } = useParams<{ id: string }>();
  const { data: project } = useProject(id);
  const { data: me } = useMe();

  // When the render was asked for, or null if we have not asked this session.
  const [requestedAt, setRequestedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Everything below is derived from those two, deliberately. The render is
  // enqueued rather than done on the request thread (bottleneck B14), so the
  // answer lands on a later read — and an effect mirroring "am I still waiting"
  // into state would render one frame claiming the opposite.
  const { data: quote } = useQuote(id, { poll: requestedAt !== null });
  const exportQuote = useExportQuote(id);

  const sent = Boolean(quote?.exported_at);
  const elapsed = requestedAt === null ? 0 : now - requestedAt;
  // A WeasyPrint call on the worker, not a queue that backs up. Past half a
  // minute it has not been slow, it has failed — and saying so beats a spinner
  // that never resolves.
  const gaveUp = requestedAt !== null && !sent && elapsed > 30_000;
  const awaiting = requestedAt !== null && !sent && !gaveUp;
  const recipient = quote?.exported_to_email || project?.initiator_email || null;

  // The only effect: a clock, so `elapsed` keeps moving between polls. It
  // synchronises React with something genuinely external rather than copying
  // state React already has.
  useEffect(() => {
    if (!awaiting) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [awaiting]);

  function onSend() {
    if (!quote) return;
    setError(null);
    setNow(Date.now());
    exportQuote.mutate(quote.id, {
      onSuccess: () => setRequestedAt(Date.now()),
      onError: (err) => setError(err instanceof ApiError ? err.message : String(err)),
    });
  }

  const notice =
    error ??
    (gaveUp
      ? "The proposal was queued but no PDF came back. The render runs on the pipeline worker — " +
        "check its logs and the dead-letter queue before sending again."
      : null);

  return (
    <EstimateShell
      project={project}
      stage={4}
      subs={{
        1: "—",
        2: "—",
        3: quote ? money2(quote.grand_total) : "—",
        4: sent ? "Sent" : quote?.status === "APPROVED" ? "Ready to send" : "Not approved",
      }}
      hint={
        sent
          ? `Sent to ${recipient}. The PDF is kept with the job.`
          : quote?.status === "APPROVED"
            ? `Goes to ${recipient ?? "the initiator"} — not a group inbox.`
            : "Approve the quote on the previous step before anything can be sent."
      }
    >
      <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "grid", gridTemplateColumns: "minmax(0,1fr) 300px", gap: "14px", padding: "14px 16px", overflow: "hidden" }}>
        <div style={{ minWidth: "0", overflow: "auto", background: "var(--app-bg)", border: "1px solid var(--app-line)", borderRadius: "16px", padding: "22px" }}>
          {sent ? (
            <div style={{ width: "100%", minWidth: "700px", maxWidth: "816px", margin: "0 auto 14px", display: "flex", alignItems: "center", gap: "12px", padding: "13px 15px", background: "var(--app-accent-soft)", border: "1px solid var(--app-accent-line)", borderRadius: "12px", animation: "fadein 220ms cubic-bezier(0.32,0.72,0,1)" }}>
              <i className="ph-duotone ph-seal-check" style={{ fontSize: "22px", color: "var(--app-accent)" }}></i>
              <span style={{ minWidth: "0", flex: 1 }}>
                <span style={{ display: "block", fontSize: "13px", fontWeight: "700", color: "var(--app-accent)" }}>
                  Sent to {recipient}
                </span>
                <span style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", lineHeight: "1.55", marginTop: "2px" }}>
                  Routed to whoever asked for the bid, never a group address. The rendered PDF is
                  stored with the job.
                </span>
              </span>
              {quote?.export_url ? (
                <a
                  href={quote.export_url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "7px", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx)", borderRadius: "9px", padding: "7px 12px", fontSize: "12px", fontWeight: 600, textDecoration: "none" }}
                >
                  <i className="ph-duotone ph-file-pdf" style={{ fontSize: "15px" }}></i>
                  Open the PDF
                </a>
              ) : null}
            </div>
          ) : null}

          {notice ? (
            <div role="alert" style={{ width: "100%", minWidth: "700px", maxWidth: "816px", margin: "0 auto 14px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "12px", padding: "11px 14px", fontSize: "12.5px", lineHeight: 1.55 }}>
              {notice}
            </div>
          ) : null}

          <Sheet quote={quote ?? null} project={project} estimator={me ?? null} />
        </div>

        <Settings
          quote={quote ?? null}
          projectId={id}
          recipient={recipient}
          sending={exportQuote.isPending || awaiting}
          onSend={onSend}
        />
      </div>
    </EstimateShell>
  );
}

/* ---------------------------------------------------------------- sheet --- */

function Sheet({
  quote,
  project,
  estimator,
}: {
  quote: Quote | null;
  project: Project | undefined;
  estimator: { full_name: string; email: string; phone: string } | null;
}) {
  const groups = groupLines(quote).filter((g) => g.key !== "FREIGHT");
  const date = quote?.approved_at ?? quote?.created_at;
  const dated = date ? new Date(date).toLocaleDateString("en-US") : "—";

  return (
    <div style={{ width: "100%", minWidth: "700px", maxWidth: "816px", margin: "0 auto", background: "#fff", color: "#111", boxShadow: "var(--app-sh-2)", padding: "34px 38px 40px", fontSize: "11px", lineHeight: "1.45" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "20px" }}>
        <div>
          <div style={{ fontSize: "19px", fontWeight: "800", letterSpacing: "-0.01em" }}>Proposal</div>
          <div style={{ fontSize: "10.5px", marginTop: "7px", color: "#333", lineHeight: "1.6" }}>
            CBC Construction Building Components — A Division of The Hamilton Parker Company
            <br />
            1865 Leonard Ave. Columbus, OH 43219 · Phone (614) 358-7800
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "auto auto", gap: "2px 12px", fontSize: "10.5px", textAlign: "right" }}>
          <span style={{ color: "#666" }}>Proposal No.</span>
          <span style={{ fontWeight: "700" }}>{quote ? quote.id.slice(0, 8).toUpperCase() : "—"}</span>
          <span style={{ color: "#666" }}>Date</span>
          <span style={{ fontWeight: "700" }}>{dated}</span>
          <span style={{ color: "#666" }}>Job</span>
          <span style={{ fontWeight: "700" }}>{project?.name ?? "—"}</span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0", marginTop: "20px", border: "1px solid #ccc" }}>
        <Block
          rows={[
            ["Customer:", project?.general_contractor || "—", true],
            ["Attention:", project?.initiator_email?.split("@")[0] || "—"],
            ["Email:", project?.initiator_email || "—"],
            ["Architect:", project?.architect || "—"],
          ]}
          border
        />
        <Block
          rows={[
            ["Brand:", project?.brand || "—", true],
            ["Channel:", project?.source_channel === "PHONE" ? "Phoned in" : project?.source_channel === "EMAIL" ? "By email" : "Entered by hand"],
            ["Bid due:", project?.due_date || "—"],
          ]}
          border
        />
        <Block
          rows={[
            ["Estimator:", estimator?.full_name || "—", true],
            ["Est Email:", estimator?.email || "—"],
            ["Est Phone:", estimator?.phone || "—"],
          ]}
        />
      </div>

      <div style={{ marginTop: "14px", fontSize: "10.5px", fontWeight: "800", letterSpacing: "0.06em" }}>ALL BIDDERS</div>
      <div style={{ marginTop: "5px", fontSize: "9.5px", color: "#444", lineHeight: "1.55" }}>{TERMS}</div>

      <div style={{ display: "grid", gridTemplateColumns: DOC, gap: "0 8px", marginTop: "18px", padding: "6px 4px", borderTop: "1px solid #111", borderBottom: "1px solid #111", fontSize: "9.5px", fontWeight: "800", letterSpacing: "0.06em" }}>
        <span>PART</span>
        <span>QTY</span>
        <span>UOM</span>
        <span>DESCRIPTION</span>
        <span style={{ textAlign: "right" }}>UNIT PRICE</span>
        <span style={{ textAlign: "right" }}>EXT. PRICE</span>
      </div>

      {groups.length ? (
        groups.map((g) => (
          <div key={g.key} style={{ marginTop: "12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "4px 4px 5px", borderBottom: "1px solid #999" }}>
              <span style={{ fontSize: "10.5px", fontWeight: "800", letterSpacing: "0.06em", whiteSpace: "nowrap" }}>
                {GROUP_LABELS[g.key]?.name.toUpperCase() ?? g.key}
              </span>
              <span style={{ fontSize: "10.5px", fontWeight: "800" }}>{money2(g.subtotal)}</span>
            </div>
            {g.lines.map((l) => (
              <div key={l.id} style={{ display: "grid", gridTemplateColumns: DOC, gap: "0 8px", padding: "4px", borderBottom: "1px solid #eee", fontSize: "9.5px" }}>
                <span style={{ fontWeight: "700", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {l.catalog_item_detail?.sku || "—"}
                </span>
                <span>{num(l.quantity)}</span>
                <span style={{ color: "#666" }}>{l.unit || "EA"}</span>
                <span style={{ minWidth: "0", color: "#333", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {l.description}
                </span>
                <span style={{ textAlign: "right" }}>{money2(l.sale_each)}</span>
                <span style={{ textAlign: "right", fontWeight: "700" }}>{money2(l.extended)}</span>
              </div>
            ))}
          </div>
        ))
      ) : (
        <div style={{ padding: "24px 4px", fontSize: "10px", color: "#888" }}>
          This quote has no lines yet.
        </div>
      )}

      <div style={{ marginTop: "16px", borderTop: "1px solid #111", paddingTop: "8px", display: "grid", gap: "4px" }}>
        <TotalRow label="QUOTE SUB-TOTAL" value={money2(quote?.subtotal_sale)} />
        <TotalRow
          label={quote?.tax_jurisdiction ? `SALES TAX · ${quote.tax_jurisdiction}` : "SALES TAX"}
          value={quote?.tax_jurisdiction ? money2(quote.tax_amount) : "NOT APPLICABLE"}
        />
        <TotalRow label="FREIGHT AND PALLET CHARGES" value={quote?.freight_display === "TBD" ? "TBD" : money2(quote?.freight_amount)} />
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "24px", fontSize: "13px", borderTop: "1px solid #111", paddingTop: "6px", marginTop: "3px" }}>
          <span style={{ letterSpacing: "0.06em", fontWeight: "800" }}>QUOTE TOTAL</span>
          <span style={{ width: "88px", textAlign: "right", fontWeight: "800" }}>{money2(quote?.grand_total)}</span>
        </div>
      </div>

      <div style={{ marginTop: "14px", fontSize: "9.5px", fontWeight: "800" }}>
        Please confirm receipt and advise of any addenda affecting this scope.
      </div>
      <div style={{ marginTop: "18px", paddingTop: "8px", borderTop: "1px solid #ddd", display: "flex", justifyContent: "space-between", fontSize: "9px", color: "#888" }}>
        <span>{plural(quote?.lines.length ?? 0, "line")}</span>
        <span>Page 1 of 1</span>
        <span>{dated}</span>
      </div>
    </div>
  );
}

function Block({ rows, border }: { rows: [string, string, boolean?][]; border?: boolean }) {
  return (
    <div style={{ padding: "8px 10px", borderRight: border ? "1px solid #ccc" : undefined }}>
      <div style={{ display: "grid", gridTemplateColumns: "64px 1fr", gap: "2px 8px", fontSize: "10px" }}>
        {rows.map(([k, v, bold]) => (
          <span key={k} style={{ display: "contents" }}>
            <span style={{ color: "#666" }}>{k}</span>
            <span style={{ fontWeight: bold ? 700 : 400, wordBreak: "break-word" }}>{v}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function TotalRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", gap: "24px", fontSize: "10.5px" }}>
      <span style={{ color: "#666", letterSpacing: "0.06em", fontWeight: "700" }}>{label}</span>
      <span style={{ width: "88px", textAlign: "right", fontWeight: "700" }}>{value}</span>
    </div>
  );
}

/* ------------------------------------------------------------- settings --- */

function Settings({
  quote,
  projectId,
  recipient,
  sending,
  onSend,
}: {
  quote: Quote | null;
  projectId: string;
  recipient: string | null;
  sending: boolean;
  onSend: () => void;
}) {
  const approved = quote?.status === "APPROVED" || quote?.status === "EXPORTED";
  const sent = Boolean(quote?.exported_at);

  const signoff: [string, string, boolean][] = [
    ["Priced", quote ? `${plural(quote.lines.length, "line")} costed and margined` : "—", Boolean(quote?.lines.length)],
    ["Approved", quote?.approved_at ? new Date(quote.approved_at).toLocaleString() : "Not yet — approve on the quote step", approved],
    ["Sent", quote?.exported_at ? new Date(quote.exported_at).toLocaleString() : `Goes to ${recipient ?? "the initiator"}`, sent],
  ];

  return (
    <div style={{ minWidth: "0", overflowY: "auto", overflowX: "hidden", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", padding: "16px" }}>
      <div style={{ fontSize: "10.5px", fontWeight: "700", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        Proposal settings
      </div>

      <Markup quote={quote} projectId={projectId} />

      <div style={{ marginTop: "20px", fontSize: "10.5px", fontWeight: "700", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        Sign-off
      </div>
      <div style={{ marginTop: "11px", borderLeft: "1px solid var(--app-line)", paddingLeft: "14px" }}>
        {signoff.map(([title, sub, done]) => (
          <div key={title} style={{ position: "relative", paddingBottom: "14px" }}>
            <span style={{ position: "absolute", left: "-19px", top: "4px", width: "9px", height: "9px", borderRadius: "50%", background: done ? "var(--app-accent)" : "var(--app-tx-3)", boxShadow: `0 0 0 3px ${done ? "var(--app-accent-soft)" : "transparent"}` }}></span>
            <span style={{ display: "block", fontSize: "12.5px", fontWeight: "600" }}>{title}</span>
            <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "2px", lineHeight: "1.5" }}>{sub}</span>
          </div>
        ))}
      </div>

      <div style={{ marginTop: "6px", fontSize: "10.5px", fontWeight: "700", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        Exclusions on the sheet
      </div>
      <div style={{ marginTop: "9px", display: "grid", gap: "7px" }}>
        {EXCLUSIONS.map((e) => (
          <span key={e} style={{ fontSize: "11.5px", color: "var(--app-tx-2)", lineHeight: "1.55" }}>
            {e}
          </span>
        ))}
      </div>

      <button
        onClick={onSend}
        disabled={!approved || sending}
        title={approved ? undefined : "A quote must be approved before anything can be sent (NFR-1)."}
        style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", marginTop: "18px", background: approved ? "linear-gradient(135deg,#818cf8,#22d3ee)" : "var(--app-panel-2)", color: approved ? "#0a0a12" : "var(--app-tx-3)", border: approved ? "0" : "1px solid var(--app-line)", borderRadius: "11px", padding: "11px", fontFamily: "var(--app-font)", fontSize: "13.5px", fontWeight: "700", cursor: approved ? (sending ? "progress" : "pointer") : "not-allowed", transition: "opacity 160ms ease" }}
      >
        <i className="ph-duotone ph-paper-plane-tilt" style={{ fontSize: "17px" }}></i>
        {sending ? "Rendering…" : sent ? "Send again" : "Send the proposal"}
      </button>
      <div style={{ marginTop: "8px", fontSize: "11.5px", color: "var(--app-tx-3)", textAlign: "center", lineHeight: 1.5 }}>
        {approved ? `Goes to ${recipient ?? "the captured initiator"}.` : "Approve the quote first."}
      </div>

      <div style={{ marginTop: "14px", fontSize: "11px", color: "var(--app-tx-3)", lineHeight: 1.55, borderTop: "1px solid var(--app-line)", paddingTop: "12px" }}>
        The final layout is still pending CBC&rsquo;s own quote workbook (open item Q10). What is
        shown here follows the specified structure; matching their sheet exactly is a template
        change.
      </div>
    </div>
  );
}

/**
 * Presentation markup.
 *
 * The prototype adjusts the displayed figures. This writes `margin_pct` and lets
 * the engine re-price, because the alternative — a document showing numbers the
 * record does not hold — is the §6.2 step 5 failure this system is built to
 * avoid. One extra round trip buys a proposal that cannot disagree with its own
 * audit trail.
 */
function Markup({ quote, projectId }: { quote: Quote | null; projectId: string }) {
  const update = useUpdateLine(projectId);
  const [busy, setBusy] = useState(false);

  const steps: [string, number][] = [
    ["As priced", 0],
    ["+2 pts", 0.02],
    ["+5 pts", 0.05],
  ];
  const locked = !quote || quote.status !== "DRAFT";

  async function apply(delta: number) {
    if (!quote || !delta) return;
    if (
      !window.confirm(
        `Add ${Math.round(delta * 100)} margin points to every priced line?\n\n` +
          "This changes the quote itself, not just how it prints — the document and the record " +
          "stay in step.",
      )
    )
      return;

    setBusy(true);
    try {
      for (const line of quote.lines) {
        if (line.line_group === "FREIGHT") continue;
        const next = Math.min(0.95, num(line.margin_pct) + delta);
        await update.mutateAsync({
          id: line.id,
          patch: {
            margin_pct: next.toFixed(4),
            margin_overridden: true,
            margin_override_reason: `Presentation markup of ${Math.round(delta * 100)} points applied at proposal stage`,
          },
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div style={{ marginTop: "13px", fontSize: "12px", color: "var(--app-tx-2)", marginBottom: "6px" }}>
        Presentation markup
      </div>
      <div style={{ display: "flex", gap: "5px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "4px" }}>
        {steps.map(([label, delta]) => (
          <button
            key={label}
            onClick={() => apply(delta)}
            disabled={locked || busy || !delta}
            style={{ flex: "1", background: "transparent", border: "0", color: locked || busy ? "var(--app-tx-3)" : "var(--app-tx)", borderRadius: "8px", padding: "7px 6px", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: "600", cursor: locked || busy || !delta ? "not-allowed" : "pointer" }}
          >
            {label}
          </button>
        ))}
      </div>
      <div style={{ marginTop: "9px", fontSize: "11.5px", color: "var(--app-tx-3)", lineHeight: "1.6" }}>
        {busy
          ? "Re-pricing every line…"
          : locked
            ? "The quote is no longer a draft, so its margins are fixed."
            : "Writes the margin onto each line and re-prices, so the sheet and the record never disagree. Each change is logged with a reason."}
      </div>
    </>
  );
}
