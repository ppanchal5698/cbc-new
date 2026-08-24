"use client";

/**
 * One row of the ledger, collapsed and expanded.
 *
 * The collapsed row is deliberately narrow: a checkbox, a state icon, the five
 * facts an estimator scans by, and a single quick action whose label changes with
 * what is wrong. Expanding it is where the reasoning lives — why this row is in
 * front of you, where it was read, and what to do about it.
 *
 * The confidence ring is a conic gradient rather than a number alone, because a
 * column of percentages is a column nobody reads.
 */

import { useState } from "react";
import { useChrome } from "@/app/providers";
import {
  itemStyle,
  origin,
  quickAction,
  useConfirmItem,
  useKeepBoth,
  useKeepOne,
  useRemoveItem,
  useUpdateItem,
  zeroTolerance,
  type ItemPatch,
} from "@/lib/ledger";
import { useProvenance } from "@/lib/openings";
import type { Opening } from "@/lib/schema";

const HEAD = "30px 40px 44px minmax(150px,1fr) 74px 44px 76px 128px";

export function LedgerRow({
  item,
  projectId,
  open,
  picked,
  onPick,
  onToggle,
  onTrace,
  onLogCall,
}: {
  item: Opening;
  projectId: string;
  open: boolean;
  picked: boolean;
  onPick: (shiftKey: boolean) => void;
  onToggle: () => void;
  onTrace: (provenanceId: string) => void;
  onLogCall: () => void;
}) {
  const style = itemStyle(item);
  const quick = quickAction(item);
  const { flash } = useChrome();

  const confirm = useConfirmItem(projectId);
  const keepOne = useKeepOne(projectId);
  const keepBoth = useKeepBoth(projectId);
  const remove = useRemoveItem(projectId);

  const label = item.door_number ? `Opening ${item.door_number}` : (item.description ?? "").slice(0, 26);

  function onQuick() {
    if (item.source_kind === "DUPLICATE") {
      keepOne.mutate(item.id, { onSuccess: () => flash("Kept one", label) });
    } else if (item.source_kind === "REVIEW") {
      confirm.mutate(item.id, { onSuccess: () => flash("Confirmed", label) });
    } else {
      onToggle();
    }
  }

  return (
    <div
      style={{
        borderBottom: "1px solid var(--app-line)",
        borderLeft: `2px solid ${open ? "var(--app-accent)" : style.bar}`,
        borderRadius: "8px",
        background: open ? "var(--app-bg-2)" : "transparent",
        marginBottom: open ? "6px" : 0,
        transition: "background 150ms ease",
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: HEAD, gap: "0 10px", alignItems: "center", padding: "7px 0 7px 6px" }}>
        <button
          onClick={(e) => onPick(e.shiftKey)}
          title="Shift-click to select a range"
          style={{ display: "grid", placeItems: "center", background: "transparent", border: 0, color: picked ? "var(--app-accent)" : "var(--app-tx-3)", cursor: "pointer" }}
        >
          <i className={picked ? "ph-duotone ph-check-square" : "ph-duotone ph-square"} style={{ fontSize: "16px" }}></i>
        </button>

        <button
          onClick={onToggle}
          title={style.headline}
          style={{ display: "grid", placeItems: "center", width: "28px", height: "28px", borderRadius: "9px", background: style.bg, border: 0, color: style.fg, cursor: "pointer" }}
        >
          <i className={style.icon} style={{ fontSize: "15px" }}></i>
        </button>

        <button onClick={onToggle} style={CELL_BTN}>
          <span style={{ fontSize: "12.5px", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            {item.door_number || "—"}
          </span>
        </button>
        <button onClick={onToggle} style={{ ...CELL_BTN, minWidth: 0 }} title={item.description}>
          <span style={{ display: "block", minWidth: 0, fontSize: "12.5px", color: "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {item.description || "—"}
          </span>
        </button>
        <button onClick={onToggle} style={CELL_BTN}>
          <span style={{ fontSize: "12px", color: "var(--app-tx-2)" }}>{item.size_raw || "—"}</span>
        </button>
        <button onClick={onToggle} style={CELL_BTN}>
          <span style={{ fontSize: "12px", fontVariantNumeric: "tabular-nums" }}>
            {item.quantity ? Number(item.quantity) : "—"}
          </span>
        </button>
        <button onClick={onToggle} style={CELL_BTN}>
          <span style={{ fontSize: "12px", color: "var(--app-tx-2)" }}>{item.hardware_group || "—"}</span>
        </button>

        <button
          onClick={onQuick}
          style={{ justifySelf: "end", display: "flex", alignItems: "center", gap: "6px", background: style.bg, border: `1px solid ${item.source_kind === "EXTRACTED" ? "transparent" : style.line}`, color: style.fg, borderRadius: "8px", padding: "4px 9px", fontFamily: "var(--app-font)", fontSize: "10.5px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap", marginRight: "6px" }}
        >
          <i className={quick.icon} style={{ fontSize: "13px" }}></i>
          {quick.label}
        </button>
      </div>

      {open ? (
        <Expanded
          item={item}
          projectId={projectId}
          style={style}
          onTrace={onTrace}
          onLogCall={onLogCall}
          onConfirm={() =>
            confirm.mutate(item.id, { onSuccess: () => flash("Item confirmed", label) })
          }
          onKeepOne={() => keepOne.mutate(item.id, { onSuccess: () => flash("Kept one", label) })}
          onKeepBoth={() =>
            keepBoth.mutate(item.id, { onSuccess: () => flash("Kept both", "Two items priced") })
          }
          onRemove={() => {
            if (!window.confirm(`Remove ${label} from this bid?`)) return;
            remove.mutate(item.id, {
              onSuccess: () => flash("Removed", "Recorded in the audit trail", true),
            });
          }}
        />
      ) : null}
    </div>
  );
}

const CELL_BTN: React.CSSProperties = {
  background: "transparent",
  border: 0,
  padding: 0,
  textAlign: "left",
  fontFamily: "var(--app-font)",
  color: "var(--app-tx)",
  cursor: "pointer",
  minWidth: 0,
};

function Expanded({
  item,
  projectId,
  style,
  onTrace,
  onLogCall,
  onConfirm,
  onKeepOne,
  onKeepBoth,
  onRemove,
}: {
  item: Opening;
  projectId: string;
  style: ReturnType<typeof itemStyle>;
  onTrace: (provenanceId: string) => void;
  onLogCall: () => void;
  onConfirm: () => void;
  onKeepOne: () => void;
  onKeepBoth: () => void;
  onRemove: () => void;
}) {
  const { data: provenance } = useProvenance(item.id);
  const update = useUpdateItem(projectId);
  const { flash } = useChrome();
  const [draft, setDraft] = useState<ItemPatch>({});

  const confidence = provenance?.length
    ? provenance.reduce((a, p) => a + (p.final_confidence ?? 0), 0) / provenance.length
    : null;

  const dirty = Object.keys(draft).length > 0;
  const value = <K extends keyof ItemPatch>(k: K): string =>
    String(draft[k] ?? (item as Record<string, unknown>)[k] ?? "");

  function save() {
    if (!dirty) return;
    update.mutate(
      { id: item.id, patch: draft },
      {
        onSuccess: () => {
          setDraft({});
          flash("Saved", "Your correction is recorded against this item");
        },
      },
    );
  }

  return (
    <div style={{ padding: "4px 12px 14px 40px", animation: "fadein 180ms ease" }}>
      {/* why this row is in front of you */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: "11px" }}>
        <span style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "9px", background: style.bg, flexShrink: 0 }}>
          <i className={style.icon} style={{ fontSize: "16px", color: style.fg }}></i>
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontSize: "13px", fontWeight: 700 }}>{style.headline}</span>
          <span style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginTop: "2px", lineHeight: 1.55 }}>
            {item.review_notes || "Read from the document as printed."}
          </span>
          <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "3px" }}>
            {origin(item)}
            {confidence !== null ? ` · ${Math.round(confidence * 100)}% confidence` : ""}
          </span>
        </span>

        {confidence !== null ? (
          <span
            title="Composite confidence across this item's fields"
            style={{ flexShrink: 0, display: "grid", placeItems: "center", width: "38px", height: "38px", borderRadius: "50%", background: `conic-gradient(${confidence < 0.8 ? "var(--app-warn)" : "var(--app-accent)"} 0turn ${confidence.toFixed(2)}turn, var(--app-panel-2) ${confidence.toFixed(2)}turn 1turn)` }}
          >
            <span style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "50%", background: "var(--app-bg-2)", fontSize: "10px", fontWeight: 700 }}>
              {Math.round(confidence * 100)}
            </span>
          </span>
        ) : null}

        <button
          onClick={() => provenance?.[0] && onTrace(provenance[0].id)}
          disabled={!provenance?.length}
          title={provenance?.length ? "Show where this was read" : "Nothing was cited for this item."}
          className="hv-f68886"
          style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "7px", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: provenance?.length ? "var(--app-tx-2)" : "var(--app-tx-3)", borderRadius: "9px", padding: "7px 11px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: 600, cursor: provenance?.length ? "pointer" : "not-allowed", whiteSpace: "nowrap" }}
        >
          <i className="ph-duotone ph-file-pdf" style={{ fontSize: "15px" }}></i>
          See it on the sheet
        </button>
      </div>

      {/* duplicates: the question the documents left open */}
      {item.source_kind === "DUPLICATE" ? (
        <div style={{ display: "flex", alignItems: "flex-start", gap: "11px", marginTop: "12px", background: "rgba(244,114,182,0.09)", border: "1px solid rgba(244,114,182,0.4)", borderRadius: "11px", padding: "11px 12px" }}>
          <i className="ph-duotone ph-copy" style={{ fontSize: "17px", color: "#f472b6", flexShrink: 0, marginTop: "1px" }}></i>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: "block", fontSize: "12.5px", fontWeight: 600 }}>
              {item.duplicate_note || "This looks like an item already read from another document."}
            </span>
            <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-2)", marginTop: "2px" }}>
              Both readings are real. Which one to price is the question — keep one, or keep both.
            </span>
          </span>
          <button onClick={onKeepOne} className="hv-f68886" style={{ ...SMALL_BTN, color: "#f472b6", borderColor: "rgba(244,114,182,0.4)" }}>
            <i className="ph-duotone ph-copy-simple" style={{ fontSize: "14px" }}></i>Keep one
          </button>
          <button onClick={onKeepBoth} className="hv-114a69" style={SMALL_BTN}>
            Keep both
          </button>
        </div>
      ) : null}

      {/* the six editable fields */}
      <div style={{ display: "grid", gridTemplateColumns: "76px minmax(0,1.5fr) 92px 56px 88px 92px", gap: "0 10px", marginTop: "13px" }}>
        {(
          [
            ["Mark", "door_number"],
            ["Description", "description"],
            ["Size", "size_raw"],
            ["Qty", "quantity"],
            ["Division", "csi_division"],
            ["HW set", "hardware_group"],
          ] as const
        ).map(([label, key]) => (
          <span key={key} style={{ minWidth: 0 }}>
            <span style={{ display: "block", fontSize: "10px", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)", marginBottom: "4px" }}>
              {label}
            </span>
            <input
              value={value(key)}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              placeholder={key === "hardware_group" ? "Search sets" : undefined}
              style={{ width: "100%", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "8px", padding: "6px 9px", fontFamily: "var(--app-font)", fontSize: "12.5px", color: "var(--app-tx)", outline: "none", boxSizing: "border-box" }}
            />
          </span>
        ))}
      </div>

      {/* zero-tolerance, read-only — off the grid, never off the record (§5.8) */}
      <div style={{ display: "flex", alignItems: "center", gap: "18px", marginTop: "12px", padding: "9px 11px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "10px" }}>
        {zeroTolerance(item).map((f) => (
          <span key={f.k} style={{ display: "flex", alignItems: "baseline", gap: "7px", minWidth: 0 }}>
            <span style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
              {f.k}
            </span>
            <span style={{ fontSize: "12.5px", color: f.warn ? "var(--app-warn)" : "var(--app-tx)", whiteSpace: "nowrap" }}>
              {f.v}
            </span>
          </span>
        ))}
        <span style={{ flex: 1 }}></span>
        <span style={{ fontSize: "11px", color: "var(--app-tx-3)", whiteSpace: "nowrap" }}>
          Corrected on the sheet, not here
        </span>
      </div>

      {/* actions */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "13px" }}>
        <button onClick={onConfirm} className="hv-f68886" style={{ ...SMALL_BTN, color: "var(--app-accent)", borderColor: "var(--app-accent-line)" }}>
          <i className="ph-duotone ph-check-circle" style={{ fontSize: "14px" }}></i>
          {item.source_kind === "REVIEW" ? "Looks right" : "Keep as is"}
        </button>
        <button
          onClick={save}
          disabled={!dirty || update.isPending}
          title={dirty ? "Write your corrections" : "Nothing has changed."}
          className="hv-f68886"
          style={{ ...SMALL_BTN, opacity: dirty ? 1 : 0.5, cursor: dirty ? "pointer" : "not-allowed" }}
        >
          <i className="ph-duotone ph-floppy-disk" style={{ fontSize: "14px" }}></i>
          {update.isPending ? "Saving…" : "Save my changes"}
        </button>
        <button onClick={onLogCall} className="hv-114a69" style={SMALL_BTN}>
          <i className="ph-duotone ph-phone-call" style={{ fontSize: "14px" }}></i>Log a call
        </button>
        <span style={{ flex: 1 }}></span>
        <button onClick={onRemove} className="hv-78b3f3" style={{ ...SMALL_BTN, color: "var(--app-neg)", borderColor: "var(--app-neg-line)" }}>
          <i className="ph-duotone ph-trash" style={{ fontSize: "14px" }}></i>Remove
        </button>
      </div>
    </div>
  );
}

const SMALL_BTN: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "7px",
  background: "var(--app-panel)",
  border: "1px solid var(--app-line)",
  color: "var(--app-tx-2)",
  borderRadius: "9px",
  padding: "7px 11px",
  fontFamily: "var(--app-font)",
  fontSize: "12px",
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
};
