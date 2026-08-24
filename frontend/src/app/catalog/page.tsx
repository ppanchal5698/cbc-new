"use client";

/**
 * Product catalog (FR-3) — ported from the "product catalog" section of the
 * Ops-Hub prototype: search, division chips, the six-column table and the
 * detail rail.
 *
 * The requirement worth keeping in view: this library is **independent of any
 * single job file**. That is the whole point of it — CBC's status quo is a part
 * number living in whichever workbook last used it, and the fix is one library
 * every quote reads from.
 */

import { useState } from "react";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { availability, divisionLabel, BAND_LABELS, useCatalogItems } from "@/lib/catalog";
import { money2, plural } from "@/lib/format";
import type { CatalogItem } from "@/lib/schema";
import { useMe } from "@/lib/session";

const COLUMNS = "200px minmax(0,1fr) 108px 96px 88px 108px";
const DIVISIONS: [string, string | null][] = [
  ["Everything", null],
  ["Openings · 08", "08"],
  ["Finishes · 09", "09"],
  ["Specialties · 10", "10"],
];

export default function CatalogPage() {
  return (
    <RequireAuth>
      <Catalog />
    </RequireAuth>
  );
}

function Catalog() {
  const [search, setSearch] = useState("");
  const [division, setDivision] = useState<string | null>(null);
  const [stockOnly, setStockOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const { data: items, isPending } = useCatalogItems({ search, division, stockOnly });
  const { data: me } = useMe();

  const rows = items ?? [];
  const detail = rows.find((r) => r.id === selected) ?? rows[0] ?? null;

  return (
    <AppShell crumbs={[{ label: "Product catalog" }]}>
      <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "grid", gridTemplateColumns: "minmax(0,1fr) 350px", overflow: "hidden" }}>
        <div style={{ minWidth: "0", display: "flex", flexDirection: "column", overflow: "hidden", borderRight: "1px solid var(--app-line)" }}>
          <div style={{ flexShrink: "0", padding: "24px 32px 12px" }}>
            <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "20px" }}>
              <div>
                <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "28px", letterSpacing: "-0.02em" }}>
                  Product catalog
                </div>
                <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "3px" }}>
                  {isPending ? "Loading…" : `${plural(rows.length, "item")} — one library, not one per job.`}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "9px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "12px", padding: "8px 12px", width: "320px" }}>
                <i className="ph-duotone ph-magnifying-glass" style={{ fontSize: "15px", color: "var(--app-tx-3)" }}></i>
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Part number, description or manufacturer"
                  style={{ flex: "1", minWidth: "0", border: "0", outline: "none", background: "transparent", fontFamily: "var(--app-font)", fontSize: "13px", color: "var(--app-tx)" }}
                />
                <span style={{ fontSize: "11px", color: "var(--app-tx-3)", fontVariantNumeric: "tabular-nums" }}>{rows.length}</span>
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "7px", marginTop: "16px", flexWrap: "wrap" }}>
              {DIVISIONS.map(([label, value]) => {
                const on = division === value;
                return (
                  <button
                    key={label}
                    onClick={() => setDivision(value)}
                    className="hv-1afc26"
                    style={{ display: "flex", alignItems: "center", gap: "7px", background: on ? "var(--app-tx)" : "transparent", border: `1px solid ${on ? "var(--app-tx)" : "var(--app-line)"}`, color: on ? "var(--app-bg-2)" : "var(--app-tx)", borderRadius: "10px", padding: "5px 11px", fontFamily: "var(--app-font)", fontSize: "12.5px", cursor: "pointer", whiteSpace: "nowrap" }}
                  >
                    {label}
                  </button>
                );
              })}
              <button
                onClick={() => setStockOnly((v) => !v)}
                title="The top-N stock list is what the system prices automatically; beyond it is the estimator's long tail by design (NR-13)."
                className="hv-1afc26"
                style={{ display: "flex", alignItems: "center", gap: "7px", background: stockOnly ? "var(--app-accent-soft)" : "transparent", border: `1px solid ${stockOnly ? "var(--app-accent-line)" : "var(--app-line)"}`, color: stockOnly ? "var(--app-accent)" : "var(--app-tx)", borderRadius: "10px", padding: "5px 11px", fontFamily: "var(--app-font)", fontSize: "12.5px", cursor: "pointer", whiteSpace: "nowrap" }}
              >
                Stocked only
              </button>
            </div>
          </div>

          <div style={{ flexShrink: "0", display: "grid", gridTemplateColumns: COLUMNS, gap: "0 12px", padding: "9px 32px", borderTop: "1px solid var(--app-line)", borderBottom: "1px solid var(--app-line)", fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)", background: "var(--app-bg-2)" }}>
            <span>Part</span>
            <span>Description</span>
            <span>Manufacturer</span>
            <span style={{ textAlign: "right" }}>List</span>
            <span>Division</span>
            <span>Availability</span>
          </div>

          <div style={{ flex: "1", minHeight: "0", overflowY: "auto", overflowX: "hidden" }}>
            {rows.map((item) => (
              <Row key={item.id} item={item} on={detail?.id === item.id} onPick={() => setSelected(item.id)} />
            ))}
            {!isPending && !rows.length ? (
              <div style={{ padding: "56px 24px", textAlign: "center" }}>
                <div style={{ fontFamily: "var(--app-font-h)", fontSize: "17px", marginBottom: "6px" }}>No parts match</div>
                <div style={{ fontSize: "12.5px", color: "var(--app-tx-3)", maxWidth: "360px", margin: "0 auto", lineHeight: "1.6" }}>
                  The library is seeded from CBC&rsquo;s stock list. Until that list lands it holds a
                  sample only, which is why matching cannot go live yet.
                </div>
              </div>
            ) : null}
          </div>
        </div>

        <Detail item={detail} isAdmin={me?.role === "ADMIN"} />
      </div>
    </AppShell>
  );
}

function Row({ item, on, onPick }: { item: CatalogItem; on: boolean; onPick: () => void }) {
  const avail = availability(item);
  return (
    <button
      onClick={onPick}
      className="hv-4c0e19"
      style={{ width: "100%", display: "grid", gridTemplateColumns: COLUMNS, gap: "0 12px", alignItems: "center", textAlign: "left", background: on ? "var(--app-accent-soft)" : "transparent", border: "0", borderBottom: "1px solid var(--app-line)", borderLeft: `2px solid ${on ? "var(--app-accent)" : "transparent"}`, padding: "10px 32px", fontFamily: "var(--app-font)", fontSize: "13px", color: "var(--app-tx)", cursor: "pointer", transition: "background 140ms ease" }}
    >
      <span style={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{item.sku}</span>
      <span style={{ minWidth: "0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{item.description}</span>
      <span style={{ fontSize: "12.5px", color: "var(--app-tx-2)" }}>{item.vendor}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{item.list_price ? money2(item.list_price) : "—"}</span>
      <span style={{ fontSize: "12px", fontVariantNumeric: "tabular-nums", color: "var(--app-tx-2)" }}>{item.csi_division || "—"}</span>
      <span style={{ fontSize: "11.5px", color: avail.fg, whiteSpace: "nowrap" }}>{avail.label}</span>
    </button>
  );
}

function Detail({ item, isAdmin }: { item: CatalogItem | null; isAdmin: boolean }) {
  if (!item) {
    return (
      <div style={{ minWidth: "0", background: "var(--app-bg-2)", padding: "22px 20px" }}>
        <div style={{ fontSize: "12.5px", color: "var(--app-tx-3)", lineHeight: 1.6 }}>
          Pick a part to see how it is priced and matched.
        </div>
      </div>
    );
  }

  const facts: [string, string, string?][] = [
    ["Manufacturer", item.vendor],
    ["Series", item.series || "—"],
    ["Part no.", item.part_number || "—"],
    ["Division", `${item.csi_division || "—"} · ${divisionLabel(item.csi_division)}`],
    ["Margin band", BAND_LABELS[item.product_type_band ?? ""] ?? item.product_type_band ?? "—"],
    ["List", item.list_price ? money2(item.list_price) : "—"],
    ["List dated", item.list_price_effective_date || "not dated"],
    ["Sheet", item.list_price_sheet_version || "—"],
    // Both halves matter: rated hardware is a distinct certified product line,
    // and handed parts are separate SKUs (§1.3).
    ["Fire rating", item.fire_rating_minutes ? `${item.fire_rating_minutes} min` : "Unrated"],
    ["Handing", item.handing || "Not handed"],
    ["Finish", item.finish_us_code ? `${item.finish_us_code} · ${item.finish_bhma_code}` : "—"],
    // Nullable on purpose (Risk R3) — P21 item ids diverge from manufacturer
    // part numbers, and a blank cell would read as data someone forgot.
    ["P21", item.p21_item_id || "not linked"],
  ];

  return (
    <div style={{ minWidth: "0", overflowY: "auto", overflowX: "hidden", background: "var(--app-bg-2)", padding: "22px 20px 28px" }}>
      <div style={{ fontSize: "10.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
        {availability(item).label}
      </div>
      <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "19px", letterSpacing: "-0.01em", marginTop: "5px", fontVariantNumeric: "tabular-nums" }}>
        {item.sku}
      </div>
      <div style={{ fontSize: "13px", color: "var(--app-tx)", marginTop: "4px", lineHeight: "1.55" }}>{item.description}</div>

      <div style={{ marginTop: "18px" }}>
        {facts.map(([k, v]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "96px minmax(0,1fr)", gap: "10px", alignItems: "baseline", padding: "7px 0", borderBottom: "1px solid var(--app-line)" }}>
            <span style={{ fontSize: "12px", color: "var(--app-tx-3)" }}>{k}</span>
            <span style={{ fontSize: "13px", fontVariantNumeric: "tabular-nums", color: v === "not linked" || v === "not dated" ? "var(--app-tx-3)" : "var(--app-tx)", wordBreak: "break-word" }}>
              {v}
            </span>
          </div>
        ))}
      </div>

      <div style={{ marginTop: "16px", fontSize: "12.5px", color: "var(--app-tx-2)", lineHeight: "1.6" }}>
        {item.is_stock
          ? "Stocked, so an opening matching this is priced automatically."
          : "Not on the stock list. Matches route to the manual path rather than proposing a line."}
      </div>

      {isAdmin ? (
        <div style={{ marginTop: "18px", fontSize: "11.5px", color: "var(--app-tx-3)", lineHeight: 1.6, borderTop: "1px solid var(--app-line)", paddingTop: "12px" }}>
          Reference data is edited in the Django admin, where every change is
          attributed. Nothing here writes silently to the library every quote reads.
        </div>
      ) : null}
    </div>
  );
}
