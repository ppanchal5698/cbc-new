"use client";

/**
 * Price books — ported from the prototype's programme list and detail.
 *
 * A multiplier on its own tells an estimator nothing about whether to trust it.
 * Risk R5 is that stale prices quietly drive real quotes, and NFR-10 still has no
 * named steward — so what this screen does is put the programme's own dates and
 * owner next to the figure they justify: when the sheet took effect, how long
 * CBC's cost is protected, who owns it, and when anyone last looked.
 *
 * Two facts the UI must never soften. Protection lapsing is not a tidy-up
 * reminder: past that date a mid-year list increase reaches the quote, which is
 * why a lapsed programme is called out rather than greyed. And the catalogue's
 * finish codes stay separate rows — US19 and US26D are different satin finishes
 * on different base metals, and estimators flagged conflating them explicitly.
 */

import { useMemo, useState } from "react";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { useChrome } from "@/app/providers";
import { useCatalogItems, useMarkReviewed, useVendorMultipliers } from "@/lib/catalog";
import { dayMonth, money2, plural } from "@/lib/format";
import type { CatalogItem, VendorMultiplier } from "@/lib/schema";
import { useMe } from "@/lib/session";

export default function BooksPage() {
  return (
    <RequireAuth>
      <Books />
    </RequireAuth>
  );
}

function Books() {
  const { data: books } = useVendorMultipliers();
  const { data: items } = useCatalogItems({});
  const [selected, setSelected] = useState<string | null>(null);

  const list = books ?? [];
  const book = list.find((b) => b.id === selected) ?? list[0] ?? null;
  const stale = list.filter((b) => b.is_stale).length;

  return (
    <AppShell crumbs={[{ label: "Price books" }]}>
      <div style={{ position: "absolute", inset: 0, minWidth: 0, display: "grid", gridTemplateColumns: "330px minmax(0,1fr)", overflow: "hidden" }}>
        <div style={{ minWidth: 0, overflowY: "auto", overflowX: "hidden", borderRight: "1px solid var(--app-line)", background: "var(--app-bg-2)", padding: "20px 0 24px" }}>
          <div style={{ padding: "0 18px 12px" }}>
            <div style={{ fontFamily: "var(--app-font-h)", fontWeight: 600, fontSize: "22px", letterSpacing: "-0.015em" }}>
              Price books
            </div>
            <div style={{ fontSize: "12.5px", color: "var(--app-tx-2)", marginTop: "3px", lineHeight: 1.5 }}>
              {plural(list.length, "multiplier programme")}
              {/* "past review" would be wrong: a programme is flagged when its
                  review date has passed *or* its cost protection has lapsed, and
                  the second is the one that reaches a quote. */}
              {stale ? ` · ${stale} needing attention` : " · all current"}
            </div>
          </div>

          {list.map((b) => {
            const on = book?.id === b.id;
            return (
              <button
                key={b.id}
                onClick={() => setSelected(b.id)}
                className="hv-b20764"
                style={{ width: "100%", textAlign: "left", background: on ? "var(--app-line)" : "transparent", border: 0, borderLeft: `2px solid ${on ? "var(--app-accent)" : b.is_stale ? "var(--app-neg)" : "transparent"}`, padding: "11px 18px", fontFamily: "var(--app-font)", cursor: "pointer", transition: "background 140ms ease" }}
              >
                <span style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "10px" }}>
                  <span style={{ fontSize: "14px", fontWeight: on ? 700 : 500 }}>{b.vendor_name}</span>
                  <span style={{ fontSize: "13px", fontVariantNumeric: "tabular-nums", color: "var(--app-accent)" }}>
                    {b.multiplier}
                  </span>
                </span>
                <span style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginTop: "2px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {b.tier || b.sheet_name || "—"}
                </span>
                <span style={{ display: "block", fontSize: "11.5px", color: b.is_stale ? "var(--app-neg)" : "var(--app-tx-3)", marginTop: "2px" }}>
                  {bookState(b)}
                </span>
              </button>
            );
          })}

          {!list.length ? (
            <div style={{ padding: "24px 18px", fontSize: "12.5px", color: "var(--app-tx-3)", lineHeight: 1.6 }}>
              Nothing seeded. Run <code style={{ fontSize: "12px" }}>make seed</code> to load the
              programmes the pricing engine reads.
            </div>
          ) : null}
        </div>

        <Detail book={book} items={items ?? []} />
      </div>
    </AppShell>
  );
}

/** What the rail says under each programme — the reason to look, or that there is none. */
function bookState(b: VendorMultiplier): string {
  if (b.protected_until && new Date(b.protected_until) < new Date()) {
    return `Protection lapsed ${dayMonth(b.protected_until)}`;
  }
  if (b.is_stale) return "Past its review date";
  if (b.protected_until) return `Protected to ${dayMonth(b.protected_until)}`;
  return b.reviewed_on ? `Reviewed ${dayMonth(b.reviewed_on)}` : "Never reviewed";
}

function Detail({ book, items }: { book: VendorMultiplier | null; items: CatalogItem[] }) {
  const { data: me } = useMe();
  const { flash } = useChrome();
  const markReviewed = useMarkReviewed();

  const priced = useMemo(
    () => (book ? items.filter((i) => i.vendor === book.vendor_name) : []),
    [book, items],
  );

  if (!book) {
    return (
      <div style={{ minWidth: 0, padding: "24px 32px", fontSize: "13px", color: "var(--app-tx-3)" }}>
        Pick a programme to see what it prices and who owns it.
      </div>
    );
  }

  const stats: { label: string; val: string; note: string; warn?: boolean }[] = [
    { label: "Effective", val: dayMonth(book.effective_date), note: "Sheet date" },
    {
      label: "Protected through",
      val: book.protected_until ? dayMonth(book.protected_until) : "—",
      note: book.protected_until ? "Cost held to this date" : "No protection",
      warn: Boolean(book.protected_until && new Date(book.protected_until) < new Date()),
    },
    {
      label: "Last reviewed",
      val: book.reviewed_on ? dayMonth(book.reviewed_on) : "Never",
      note: book.is_stale ? "Past its review date" : "Current",
      warn: book.is_stale,
    },
    {
      label: "Steward",
      val: book.steward || "Unassigned",
      note: book.steward ? "Owns this sheet" : "NFR-10 is still open",
      warn: !book.steward,
    },
  ];

  return (
    <div style={{ minWidth: 0, overflowY: "auto", overflowX: "hidden", padding: "24px 32px 34px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "20px" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: 600, fontSize: "26px", letterSpacing: "-0.02em" }}>
            {book.sheet_name || `${book.vendor_name} ${book.tier}`.trim()}
          </div>
          <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "3px" }}>
            {book.vendor_name}
            {book.tier ? ` · ${book.tier}` : ""} · sheet {book.source_sheet_version || "not recorded"}
          </div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: "11px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
            Multiplier
          </div>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: 600, fontSize: "34px", letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
            {book.multiplier}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: "1px", marginTop: "24px", background: "var(--app-line)" }}>
        {stats.map((s) => (
          <div key={s.label} style={{ background: "var(--app-bg)", padding: "14px 16px 16px" }}>
            <div style={{ fontSize: "11px", letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
              {s.label}
            </div>
            <div style={{ fontSize: "16px", fontVariantNumeric: "tabular-nums", marginTop: "6px", color: s.warn ? "var(--app-neg)" : "var(--app-tx)" }}>
              {s.val}
            </div>
            <div style={{ fontSize: "11.5px", color: "var(--app-tx-2)", marginTop: "2px" }}>{s.note}</div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: "26px", fontSize: "11px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        Priced under this program
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "210px minmax(0,1fr) 96px 96px", gap: "0 14px", marginTop: "10px", padding: "9px 8px", borderBottom: "1px solid var(--app-line)", fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        <span>Part</span>
        <span>Description</span>
        <span style={{ textAlign: "right" }}>List</span>
        <span style={{ textAlign: "right" }}>Net</span>
      </div>
      {priced.map((i) => {
        const list = Number(i.list_price ?? 0);
        return (
          <div key={i.id} className="hv-40d530" style={{ display: "grid", gridTemplateColumns: "210px minmax(0,1fr) 96px 96px", gap: "0 14px", alignItems: "center", padding: "10px 8px", borderBottom: "1px solid var(--app-line)" }}>
            <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{i.sku}</span>
            <span style={{ minWidth: 0, fontSize: "13px", color: "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{i.description}</span>
            <span style={{ fontSize: "13px", fontVariantNumeric: "tabular-nums", textAlign: "right", color: "var(--app-tx-3)" }}>
              {list ? money2(list) : "—"}
            </span>
            <span style={{ fontSize: "13px", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
              {list ? money2(list * Number(book.multiplier)) : "—"}
            </span>
          </div>
        );
      })}
      {!priced.length ? (
        <div style={{ padding: "22px 8px", fontSize: "12.5px", color: "var(--app-tx-3)", lineHeight: 1.6 }}>
          Nothing in the catalogue prices under this programme yet. The library is a
          sample until CBC&rsquo;s stock list lands.
        </div>
      ) : null}

      {book.note ? (
        <div style={{ marginTop: "22px", maxWidth: "640px", fontSize: "13px", color: "var(--app-tx-2)", lineHeight: 1.7 }}>
          {book.note}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: "9px", marginTop: "18px" }}>
        <button
          onClick={() =>
            markReviewed.mutate(book.id, {
              onSuccess: () => flash("Marked as reviewed", book.sheet_name || book.vendor_name),
            })
          }
          disabled={me?.role !== "ADMIN" || markReviewed.isPending}
          title={
            me?.role === "ADMIN"
              ? "Records that someone checked this sheet against the vendor today"
              : "Reference data is maintained by an admin."
          }
          style={{ background: me?.role === "ADMIN" ? "var(--app-accent)" : "var(--app-panel-2)", color: me?.role === "ADMIN" ? "#fff" : "var(--app-tx-3)", border: me?.role === "ADMIN" ? 0 : "1px solid var(--app-line)", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", cursor: me?.role === "ADMIN" ? "pointer" : "not-allowed" }}
        >
          {markReviewed.isPending ? "Recording…" : "Mark as reviewed today"}
        </button>
        <button
          onClick={() =>
            flash(
              "Ask purchasing for the current sheet",
              `${book.vendor_name} · ${book.sheet_name || book.tier}`,
              true,
            )
          }
          className="hv-b20764"
          style={{ background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx)", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", cursor: "pointer" }}
        >
          Request an updated sheet
        </button>
      </div>

      <div style={{ marginTop: "16px", maxWidth: "640px", fontSize: "12px", color: "var(--app-tx-3)", lineHeight: 1.7 }}>
        Marking a sheet reviewed records that someone checked it. It does not fetch a
        new one — no automatic refresh exists anywhere in the pricing path, because a
        price that moves underneath an estimator without their knowledge is the
        failure the freshness window is there to catch.
      </div>
    </div>
  );
}
