"use client";

/**
 * Stage 2 · Extraction and entry — ported from the prototype's ledger.
 *
 * This is the screen an estimator spends the most time on, and the prototype's
 * design is built around one idea: a bid set produces a single list of line
 * items, and every row is in one of four states that each want a different
 * action. Read cleanly, read twice, needs a look, or typed in by hand.
 *
 * Two things the earlier version got wrong and this restores:
 *
 *  - The list spans **Division 06, 08 and 10**. Grab bars, mirrors, hand dryers
 *    and FRP trim sit alongside the doors, because that is what arrives in a bid
 *    set and what an estimator prices.
 *  - **Duplicates are a first-class state.** An addendum reissues a schedule row
 *    while the base row is still in the list; a restroom plan repeats six grab
 *    bars the fixture schedule already counted. Both readings are real and which
 *    to price is a question the documents leave open, so the row asks.
 *
 * The zero-tolerance fields — fire rating, handing, finish (§5.8) — are not
 * columns here, exactly as the prototype has it. They are in the expanded panel
 * on every row, where "Not stated" is a finding rather than a blank.
 */

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EstimateShell, useEstimateNotes } from "@/components/estimate/EstimateShell";
import { LedgerRow } from "@/components/ledger/LedgerRow";
import { SheetViewer } from "@/components/estimate/SheetViewer";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { useChrome } from "@/app/providers";
import { useDocuments } from "@/lib/documents";
import { plural } from "@/lib/format";
import {
  LEDGER_FILTERS,
  useAddItem,
  useBulkConfirm,
  useBulkRemove,
  useConfirmAll,
  useLedger,
  useReprocess,
  type LedgerFilter,
} from "@/lib/ledger";
import { useSourceRegion } from "@/lib/openings";
import { useProject } from "@/lib/projects";
import { useCatalogItems } from "@/lib/catalog";
import type { Opening } from "@/lib/schema";

export const HEAD = "30px 40px 44px minmax(150px,1fr) 74px 44px 76px 128px";

export default function LinesPage() {
  return (
    <RequireAuth>
      <Ledger />
    </RequireAuth>
  );
}

function Ledger() {
  const { id } = useParams<{ id: string }>();
  const { data: project } = useProject(id);
  const { data: documents } = useDocuments(id);
  const { data: items, isPending } = useLedger(id);
  const { flash } = useChrome();
  const notes = useEstimateNotes();

  const [filter, setFilter] = useState<LedgerFilter>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [lastPick, setLastPick] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [activeDocument, setActiveDocument] = useState<string | undefined>();
  const [tracing, setTracing] = useState<string | null>(null);
  const [ranAt, setRanAt] = useState(false);

  const { data: region } = useSourceRegion(tracing ?? undefined);

  const confirmAll = useConfirmAll(id);
  const bulkConfirm = useBulkConfirm(id);
  const bulkRemove = useBulkRemove(id);
  const reprocess = useReprocess(id);
  const addItem = useAddItem(id);

  const rows = useMemo(() => items ?? [], [items]);
  const counts = useMemo(() => {
    const by = (kind: string) => rows.filter((r) => r.source_kind === kind).length;
    return {
      all: rows.length,
      review: by("REVIEW"),
      dup: by("DUPLICATE"),
      manual: by("MANUAL"),
      extracted: by("EXTRACTED"),
    };
  }, [rows]);

  const shown = useMemo(() => {
    const spec = LEDGER_FILTERS.find((f) => f.key === filter);
    if (!spec?.source) return rows;
    return rows.filter((r) => r.source_kind === spec.source);
  }, [filter, rows]);

  const shownIds = useMemo(() => shown.map((r) => r.id), [shown]);

  /** Shift extends from the last pick, which is how a long list gets triaged. */
  const pick = useCallback(
    (itemId: string, shiftKey: boolean) => {
      setPicked((current) => {
        if (shiftKey && lastPick) {
          const a = shownIds.indexOf(lastPick);
          const b = shownIds.indexOf(itemId);
          if (a > -1 && b > -1) {
            const range = shownIds.slice(Math.min(a, b), Math.max(a, b) + 1);
            return [...new Set([...current, ...range])];
          }
        }
        return current.includes(itemId)
          ? current.filter((x) => x !== itemId)
          : [...current, itemId];
      });
      setLastPick(itemId);
    },
    [lastPick, shownIds],
  );

  // -- keyboard: j/k move, Enter confirms, e expands, c logs a call ----------
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      const at = shownIds.indexOf(selected ?? "");
      const key = e.key.toLowerCase();

      if (key === "j" || key === "k") {
        e.preventDefault();
        const next = key === "j" ? Math.min(shownIds.length - 1, at + 1) : Math.max(0, at - 1);
        setSelected(shownIds[next] ?? null);
      } else if (key === "c" && selected) {
        e.preventDefault();
        const row = rows.find((r) => r.id === selected);
        notes.open(refOf(row), "ARCHITECT_CALL");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shownIds, selected, rows, notes]);

  const needsWork = counts.review + counts.dup;

  return (
    <EstimateShell
      project={project}
      stage={2}
      subs={{
        1: plural(documents?.length ?? 0, "document"),
        2: counts.review ? `${counts.review} to check` : rows.length ? "All clear" : "Nothing yet",
        3: "—",
        4: "—",
      }}
      hint={
        needsWork
          ? `${plural(needsWork, "item")} need a look. Open one, check it against the sheet, confirm.`
          : "Everything is checked. Pricing is next."
      }
    >
      <div style={{ position: "absolute", inset: 0, minWidth: 0, display: "grid", gridTemplateColumns: sheetOpen ? "minmax(0,1.25fr) minmax(380px,1fr)" : "minmax(0,1fr)", gap: "14px", padding: "14px 16px", overflow: "hidden", transition: "grid-template-columns 240ms cubic-bezier(0.32,0.72,0,1)" }}>
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)" }}>
          {/* header */}
          <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "12px", padding: "14px 16px 12px" }}>
            <span style={{ display: "grid", placeItems: "center", width: "36px", height: "36px", borderRadius: "11px", background: "var(--app-accent-soft)" }}>
              <i className="ph-duotone ph-list-checks" style={{ fontSize: "19px", color: "var(--app-accent)" }}></i>
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: "block", fontSize: "15px", fontWeight: 700, letterSpacing: "-0.01em" }}>Line items</span>
              <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "1px" }}>
                {counts.extracted} of {counts.all} items clear · <Key>J K</Key> to move ·{" "}
                <Key>↵</Key> confirm · <Key>C</Key> log a call
              </span>
            </span>

            <Chip
              icon="ph-duotone ph-arrows-clockwise"
              label={reprocess.isPending ? "Re-reading…" : "Re-run extraction"}
              disabled={!documents?.length || reprocess.isPending}
              title={documents?.length ? "Read the bid set again" : "Nothing uploaded yet."}
              onClick={() => {
                const doc = documents?.[0];
                if (!doc) return;
                reprocess.mutate(doc.id, {
                  onSuccess: () => {
                    setRanAt(true);
                    flash("Re-reading the bid set", doc.filename, true);
                  },
                });
              }}
            />

            {counts.review > 0 ? (
              <Chip
                icon="ph-duotone ph-checks"
                label={`Confirm all ${counts.review}`}
                onClick={() =>
                  confirmAll.mutate(id, {
                    onSuccess: () =>
                      flash(`${plural(counts.review, "item")} confirmed`, "Nothing left to check"),
                  })
                }
              />
            ) : null}

            <Chip
              icon="ph-duotone ph-file-pdf"
              label={sheetOpen ? "Hide the sheet" : "Open the sheet"}
              on={sheetOpen}
              onClick={() => setSheetOpen((v) => !v)}
            />
          </div>

          {/* filters */}
          <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "8px", padding: "0 16px 12px" }}>
            <div style={{ flex: 1, display: "flex", alignItems: "center", gap: "5px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "4px" }}>
              {LEDGER_FILTERS.map((f) => {
                const on = filter === f.key;
                return (
                  <button
                    key={f.key}
                    onClick={() => setFilter(f.key)}
                    style={{ display: "flex", alignItems: "center", gap: "6px", background: on ? "var(--app-tx)" : "transparent", border: 0, color: on ? "var(--app-bg-2)" : "var(--app-tx)", borderRadius: "8px", padding: "6px 11px", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap", transition: "all 180ms cubic-bezier(0.32,0.72,0,1)" }}
                  >
                    <i className={f.icon} style={{ fontSize: "15px" }}></i>
                    {f.label}
                    <span style={{ fontSize: "11px", color: "var(--app-tx-3)" }}>{counts[f.key]}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {ranAt ? (
            <RunStatus
              text={`${plural(rows.length, "item")} from ${plural(documents?.length ?? 0, "document")}`}
              detail={`${counts.review} need a look · ${counts.dup} possible duplicates`}
              onDismiss={() => setRanAt(false)}
            />
          ) : null}

          {picked.length ? (
            <BulkBar
              n={picked.length}
              onAll={() => setPicked(shownIds)}
              onClear={() => setPicked([])}
              onConfirm={() =>
                bulkConfirm.mutate(picked, {
                  onSuccess: () => {
                    flash(`${plural(picked.length, "item")} confirmed`, "Cleared in one go");
                    setPicked([]);
                  },
                })
              }
              onRemove={() => {
                if (!window.confirm(`Remove ${plural(picked.length, "item")} from this bid?`)) return;
                bulkRemove.mutate(picked, {
                  onSuccess: () => {
                    flash(`${plural(picked.length, "item")} removed`, "Recorded in the audit trail", true);
                    setPicked([]);
                  },
                });
              }}
            />
          ) : null}

          {/* column heads */}
          <div style={{ flexShrink: 0, display: "grid", gridTemplateColumns: HEAD, gap: "0 10px", padding: "0 16px 8px", fontSize: "10px", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
            <span></span>
            <span></span>
            <span>Mark</span>
            <span>Description</span>
            <span>Size</span>
            <span>Qty</span>
            <span>HW set</span>
            <span style={{ textAlign: "right" }}>Status</span>
          </div>

          <div style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden", padding: "0 16px 8px" }}>
            {shown.map((item) => (
              <LedgerRow
                key={item.id}
                item={item}
                projectId={id}
                open={selected === item.id}
                picked={picked.includes(item.id)}
                onPick={(shiftKey) => pick(item.id, shiftKey)}
                onToggle={() => setSelected(selected === item.id ? null : item.id)}
                onTrace={(provenanceId) => {
                  setTracing(provenanceId);
                  setSheetOpen(true);
                }}
                onLogCall={() => notes.open(refOf(item), "ARCHITECT_CALL")}
              />
            ))}

            {!shown.length && !isPending ? (
              <Empty
                filter={filter}
                anyAtAll={rows.length > 0}
              />
            ) : null}
          </div>

          <Composer
            projectId={id}
            onAdded={(what) => flash("Added by hand", what, true)}
            add={addItem}
          />
        </div>

        {sheetOpen ? (
          <SheetViewer
            documents={documents ?? []}
            activeDocumentId={activeDocument ?? documents?.[0]?.id}
            onPickDocument={setActiveDocument}
            region={region ?? null}
            onClose={() => setSheetOpen(false)}
          />
        ) : null}
      </div>
    </EstimateShell>
  );
}

/* ------------------------------------------------------------------ bits --- */

const refOf = (item: Opening | undefined): string =>
  item ? (item.door_number ? `Opening ${item.door_number}` : (item.description || "").slice(0, 28)) : "";

function Key({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontFamily: "var(--app-font)", fontSize: "10px", fontWeight: 700, color: "var(--app-tx-2)", background: "var(--app-panel-2)", borderRadius: "5px", padding: "1px 5px" }}>
      {children}
    </span>
  );
}

function Chip({
  icon,
  label,
  on,
  disabled,
  title,
  onClick,
}: {
  icon: string;
  label: string;
  on?: boolean;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="hv-f68886"
      style={{ display: "flex", alignItems: "center", gap: "7px", background: on ? "var(--app-accent-soft)" : "var(--app-panel-2)", border: `1px solid ${on ? "var(--app-accent-line)" : "var(--app-line)"}`, color: disabled ? "var(--app-tx-3)" : on ? "var(--app-accent)" : "var(--app-tx-2)", borderRadius: "10px", padding: "8px 12px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer", whiteSpace: "nowrap", transition: "all 170ms cubic-bezier(0.32,0.72,0,1)" }}
    >
      <i className={icon} style={{ fontSize: "15px" }}></i>
      {label}
    </button>
  );
}

function RunStatus({ text, detail, onDismiss }: { text: string; detail: string; onDismiss: () => void }) {
  return (
    <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "10px", margin: "0 16px 12px", background: "var(--app-accent-soft)", border: "1px solid var(--app-accent-line)", borderRadius: "11px", padding: "10px 12px" }}>
      <i className="ph-duotone ph-lightning" style={{ fontSize: "17px", color: "var(--app-accent)", flexShrink: 0 }}></i>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: "block", fontSize: "12.5px", fontWeight: 600 }}>{text}</span>
        <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-2)", marginTop: "1px" }}>{detail}</span>
      </span>
      <button
        onClick={onDismiss}
        className="hv-114a69"
        style={{ display: "grid", placeItems: "center", width: "26px", height: "26px", borderRadius: "8px", background: "transparent", border: 0, color: "var(--app-tx-3)", cursor: "pointer" }}
      >
        <i className="ph-duotone ph-x" style={{ fontSize: "14px" }}></i>
      </button>
    </div>
  );
}

function BulkBar({
  n,
  onAll,
  onClear,
  onConfirm,
  onRemove,
}: {
  n: number;
  onAll: () => void;
  onClear: () => void;
  onConfirm: () => void;
  onRemove: () => void;
}) {
  const BTN: React.CSSProperties = {
    background: "var(--app-panel)",
    border: "1px solid var(--app-line)",
    color: "var(--app-tx-2)",
    borderRadius: "9px",
    padding: "6px 11px",
    fontFamily: "var(--app-font)",
    fontSize: "12px",
    fontWeight: 600,
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
  return (
    <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "9px", margin: "0 16px 12px", background: "var(--app-bg-2)", border: "1px solid var(--app-accent-line)", borderRadius: "11px", padding: "9px 12px" }}>
      <span style={{ fontSize: "12.5px", fontWeight: 700 }}>{n} selected</span>
      <button onClick={onAll} className="hv-114a69" style={{ ...BTN, border: 0, background: "transparent" }}>
        Select all
      </button>
      <span style={{ flex: 1 }}></span>
      <button onClick={onConfirm} className="hv-f68886" style={{ ...BTN, display: "flex", alignItems: "center", gap: "6px" }}>
        <i className="ph-duotone ph-checks" style={{ fontSize: "14px" }}></i>Confirm {n}
      </button>
      <button onClick={onRemove} className="hv-78b3f3" style={{ ...BTN, display: "flex", alignItems: "center", gap: "6px", color: "var(--app-neg)", borderColor: "var(--app-neg-line)" }}>
        <i className="ph-duotone ph-trash" style={{ fontSize: "14px" }}></i>Remove
      </button>
      <button onClick={onClear} className="hv-114a69" style={{ ...BTN, border: 0, background: "transparent" }}>
        Clear
      </button>
    </div>
  );
}

/**
 * "Add anything the drawings do not carry."
 *
 * Searches the catalogue as you type and adds the picked part, or a blank line
 * to describe by hand. Either way the item is marked as entered by hand rather
 * than extracted — it has no citation and never will.
 */
function Composer({
  projectId,
  onAdded,
  add,
}: {
  projectId: string;
  onAdded: (what: string) => void;
  add: ReturnType<typeof useAddItem>;
}) {
  const [query, setQuery] = useState("");
  const { data: hits } = useCatalogItems({ search: query.trim().length > 1 ? query.trim() : "" });
  const shown = query.trim().length > 1 ? (hits ?? []).slice(0, 5) : [];

  function addLine(item?: (typeof shown)[number]) {
    add.mutate(
      {
        project: projectId,
        description: item ? item.description : "New line — describe the item",
        csi_division: item?.csi_division ?? "",
        quantity: "1",
        quote_text: item ? `${item.sku} — ${item.description}` : "",
        cell_label: item ? item.sku : "Blank line",
      },
      {
        onSuccess: () => {
          setQuery("");
          onAdded(item ? item.sku : "Blank line");
        },
      },
    );
  }

  return (
    <div style={{ flexShrink: 0, padding: "10px 16px 14px", borderTop: "1px solid var(--app-line)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "9px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "8px 12px" }}>
        <i className="ph-duotone ph-plus-circle" style={{ fontSize: "17px", color: "var(--app-tx-3)" }}></i>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addLine(shown[0]);
            }
          }}
          placeholder="Add anything the drawings do not carry — search a part number or type a description"
          style={{ flex: 1, minWidth: 0, border: 0, outline: "none", background: "transparent", fontFamily: "var(--app-font)", fontSize: "13px", color: "var(--app-tx)" }}
        />
        <span style={{ fontSize: "10.5px", color: "var(--app-tx-3)", whiteSpace: "nowrap" }}>↵ add</span>
        <button
          onClick={() => addLine()}
          className="hv-f68886"
          style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", borderRadius: "9px", padding: "6px 11px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}
        >
          Add line
        </button>
      </div>

      {shown.length ? (
        <div style={{ marginTop: "8px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "11px", overflow: "hidden" }}>
          {shown.map((h) => (
            <button
              key={h.id}
              onClick={() => addLine(h)}
              className="hv-4c0e19"
              style={{ width: "100%", display: "grid", gridTemplateColumns: "minmax(0,180px) minmax(0,1fr) 92px", gap: "12px", alignItems: "center", textAlign: "left", background: "transparent", border: 0, borderBottom: "1px solid var(--app-line)", padding: "9px 12px", fontFamily: "var(--app-font)", fontSize: "12.5px", color: "var(--app-tx)", cursor: "pointer" }}
            >
              <span style={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.sku}</span>
              <span style={{ minWidth: 0, color: "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.description}</span>
              <span style={{ fontSize: "11.5px", color: "var(--app-tx-3)", textAlign: "right" }}>{h.vendor}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Empty({ filter, anyAtAll }: { filter: LedgerFilter; anyAtAll: boolean }) {
  const note = !anyAtAll
    ? "Upload a bid set and the schedules, elevations and specs are read for you."
    : filter === "manual"
      ? "Nothing has been added by hand. The composer below is how you would."
      : filter === "dup"
        ? "Nothing was read twice. Every item came from one place."
        : filter === "review"
          ? "Nothing is waiting on you."
          : "Nothing in this view.";

  return (
    <div style={{ display: "grid", placeItems: "center", padding: "56px 20px 40px", textAlign: "center" }}>
      <span style={{ display: "grid", placeItems: "center", width: "56px", height: "56px", borderRadius: "17px", background: "var(--app-accent-soft)", marginBottom: "14px" }}>
        <i className="ph-duotone ph-pencil-line" style={{ fontSize: "26px", color: "var(--app-accent)" }}></i>
      </span>
      <div style={{ fontSize: "17px", fontWeight: 700, letterSpacing: "-0.015em" }}>Nothing in this view</div>
      <div style={{ fontSize: "12.5px", color: "var(--app-tx-3)", maxWidth: "360px", marginTop: "6px", lineHeight: 1.6 }}>{note}</div>
    </div>
  );
}
