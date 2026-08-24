"use client";

/**
 * Price books — the five reference tables that turn a matched part into money
 * (§7.5), ported from the "price books" section of the Ops-Hub prototype.
 *
 * **Every row shows its effective date, and multipliers show their sheet
 * version.** NFR-3 requires a quote line to be traceable to the tier *and* the
 * sheet that produced it, and Risk R5 is that this data goes stale with nobody
 * named to own it. Putting the dates on the surface is the cheapest form of that
 * guardrail: a book nobody has refreshed since last year says so in the list.
 *
 * Two things this screen must never soften:
 *
 *  - **US19 and US26D are different rows.** Estimators flagged it explicitly —
 *    different satin finishes on different base metals, different BHMA codes. A
 *    UI that grouped them by the word "satin" would recreate the exact
 *    conflation the finish table exists to prevent.
 *  - **Tax is Ohio and Kentucky only.** The other 48 states and Canada are
 *    untaxed because CBC sells to a GC, not an end customer.
 */

import { useState } from "react";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { BAND_LABELS, useFinishCodes, useMarginBands, useTaxRates, useThroatDepths, useVendorMultipliers } from "@/lib/catalog";
import { plural } from "@/lib/format";

type Book = "multipliers" | "margins" | "finishes" | "throats" | "taxes";

const BOOKS: { key: Book; label: string; blurb: string }[] = [
  { key: "multipliers", label: "Vendor multipliers", blurb: "List × multiplier, by negotiated tier." },
  { key: "margins", label: "Margin bands", blurb: "Applied as a divisor, not a markup." },
  { key: "finishes", label: "Finish codes", blurb: "The two nomenclatures in simultaneous use." },
  { key: "throats", label: "Throat depths", blurb: "Frame depth by wall type." },
  { key: "taxes", label: "Tax rates", blurb: "Ohio and Kentucky only." },
];

export default function BooksPage() {
  return (
    <RequireAuth>
      <Books />
    </RequireAuth>
  );
}

function Books() {
  const [book, setBook] = useState<Book>("multipliers");

  return (
    <AppShell crumbs={[{ label: "Price books" }]}>
      <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "grid", gridTemplateColumns: "330px minmax(0,1fr)", overflow: "hidden" }}>
        <div style={{ minWidth: "0", overflowY: "auto", overflowX: "hidden", borderRight: "1px solid var(--app-line)", background: "var(--app-bg-2)", padding: "20px 0 24px" }}>
          <div style={{ padding: "0 18px 12px" }}>
            <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "22px", letterSpacing: "-0.015em" }}>Price books</div>
            <div style={{ fontSize: "12.5px", color: "var(--app-tx-2)", marginTop: "3px", lineHeight: "1.5" }}>
              What every quote line is priced against. Each row carries the date it took effect.
            </div>
          </div>

          {BOOKS.map((b) => {
            const on = book === b.key;
            return (
              <button
                key={b.key}
                onClick={() => setBook(b.key)}
                className="hv-b20764"
                style={{ width: "100%", textAlign: "left", background: on ? "var(--app-line)" : "transparent", border: "0", borderLeft: `2px solid ${on ? "var(--app-accent)" : "transparent"}`, padding: "11px 18px", fontFamily: "var(--app-font)", cursor: "pointer", transition: "background 140ms ease" }}
              >
                <span style={{ display: "block", fontSize: "14px", fontWeight: on ? 700 : 500 }}>{b.label}</span>
                <span style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginTop: "2px" }}>{b.blurb}</span>
              </button>
            );
          })}
        </div>

        <div style={{ minWidth: "0", overflowY: "auto", overflowX: "hidden", padding: "24px 32px 34px" }}>
          {book === "multipliers" ? <Multipliers /> : null}
          {book === "margins" ? <Margins /> : null}
          {book === "finishes" ? <Finishes /> : null}
          {book === "throats" ? <Throats /> : null}
          {book === "taxes" ? <Taxes /> : null}
        </div>
      </div>
    </AppShell>
  );
}

/* ---------------------------------------------------------------- books --- */

function Multipliers() {
  const { data } = useVendorMultipliers();
  return (
    <Book
      title="Vendor multipliers"
      sub="Cost is list × multiplier, keyed by vendor, tier and effective date. MAP is never used as cost — it governs advertising, not what CBC pays."
      columns="180px 150px 110px 130px minmax(0,1fr)"
      head={["Vendor", "Tier", "Multiplier", "Effective", "Sheet version"]}
      rows={(data ?? []).map((m) => ({
        key: m.id,
        cells: [
          m.vendor_name,
          m.tier || "—",
          m.multiplier ?? "—",
          m.effective_date ?? "not dated",
          // NFR-3 wants the sheet version alongside the tier, not instead of it.
          m.source_sheet_version || "not recorded",
        ],
        dim: !m.source_sheet_version,
      }))}
    />
  );
}

function Margins() {
  const { data } = useMarginBands();
  return (
    <Book
      title="Margin bands"
      sub="Applied as a divisor: sale = cost ÷ (1 − margin). Stable for fourteen years, overridable per line with a logged reason."
      columns="240px 130px 130px 130px minmax(0,1fr)"
      head={["Product band", "Target", "Floor", "Effective", "Divisor"]}
      rows={(data ?? []).map((b) => ({
        key: b.id,
        cells: [
          BAND_LABELS[b.product_type_band ?? ""] ?? b.product_type_band ?? "—",
          pct(b.target_margin_pct),
          pct(b.floor_margin_pct),
          b.effective_date ?? "not dated",
          b.target_margin_pct ? (1 - Number(b.target_margin_pct)).toFixed(2) : "—",
        ],
      }))}
    />
  );
}

function Finishes() {
  const { data } = useFinishCodes();
  return (
    <Book
      title="Finish codes"
      sub="Two naming systems in simultaneous use, and both have to be interpreted. US19 and US26D are different satin finishes on different base metals — they are separate rows and must never collapse into one."
      columns="120px 120px minmax(0,1fr) 160px"
      head={["US code", "BHMA", "Description", "Base metal"]}
      rows={(data ?? []).map((f) => ({
        key: f.id,
        cells: [f.us_code, f.bhma_code, f.description, f.base_metal || "—"],
      }))}
    />
  );
}

function Throats() {
  const { data } = useThroatDepths();
  return (
    <Book
      title="Throat depths"
      sub="Frame depth by wall type. Five standards cover the large majority; anything else is entered by hand — a table, deliberately not a hardcoded pick-list."
      columns="160px 260px 130px minmax(0,1fr)"
      head={["Depth", "Wall type", "Custom", "Notes"]}
      rows={(data ?? []).map((t) => ({
        key: t.id,
        cells: [
          t.throat_depth_inches ? `${t.throat_depth_inches}"` : "—",
          t.wall_type,
          t.is_custom ? "Manual entry" : "Standard",
          t.notes || "—",
        ],
      }))}
    />
  );
}

function Taxes() {
  const { data } = useTaxRates();
  return (
    <Book
      title="Tax rates"
      sub="Ohio and Kentucky only. Everywhere else is untaxed because the sale is to a general contractor or corporation, not the end customer — and Ohio's rate is county-dependent, so it is reference data with a date rather than a constant."
      columns="140px 140px 160px minmax(0,1fr)"
      head={["Jurisdiction", "Rate", "Effective", "Description"]}
      rows={(data ?? []).map((t) => ({
        key: t.id,
        cells: [t.jurisdiction, pct(t.rate_pct), t.effective_date ?? "not dated", t.description || "—"],
      }))}
    />
  );
}

/* ---------------------------------------------------------------- frame --- */

interface BookRow {
  key: string;
  cells: (string | number | null | undefined)[];
  dim?: boolean;
}

function Book({
  title,
  sub,
  columns,
  head,
  rows,
}: {
  title: string;
  sub: string;
  columns: string;
  head: string[];
  rows: BookRow[];
}) {
  return (
    <>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "20px" }}>
        <div style={{ maxWidth: "640px" }}>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "26px", letterSpacing: "-0.02em" }}>{title}</div>
          <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "5px", lineHeight: 1.6 }}>{sub}</div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: "11px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Rows</div>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "34px", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
            {rows.length}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: columns, gap: "0 14px", marginTop: "22px", padding: "9px 8px", borderBottom: "1px solid var(--app-line)", fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        {head.map((h) => (
          <span key={h}>{h}</span>
        ))}
      </div>

      {rows.map((r) => (
        <div key={r.key} className="hv-40d530" style={{ display: "grid", gridTemplateColumns: columns, gap: "0 14px", alignItems: "center", padding: "10px 8px", borderBottom: "1px solid var(--app-line)" }}>
          {r.cells.map((c, i) => (
            <span
              key={i}
              style={{ fontSize: "13px", fontVariantNumeric: "tabular-nums", color: i === 0 ? "var(--app-tx)" : "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", opacity: r.dim && i === r.cells.length - 1 ? 0.6 : 1 }}
              title={String(c ?? "")}
            >
              {c ?? "—"}
            </span>
          ))}
        </div>
      ))}

      {!rows.length ? (
        <div style={{ padding: "48px 8px", fontSize: "13px", color: "var(--app-tx-3)", lineHeight: 1.6, maxWidth: "460px" }}>
          Nothing seeded here yet. Run <code style={{ fontSize: "12px" }}>make seed</code> to load the
          reference data the pricing engine reads.
        </div>
      ) : null}

      <div style={{ marginTop: "22px", maxWidth: "640px", fontSize: "12.5px", color: "var(--app-tx-3)", lineHeight: "1.7" }}>
        These tables are edited in the Django admin, where every change is attributed to a person.
        No named data steward exists yet, so the effective dates above are the only thing standing
        between a lapsed sheet and a wrong quote — {plural(rows.length, "row")} shown.
      </div>
    </>
  );
}

const pct = (v: string | null | undefined) => (v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(2)}%`);
