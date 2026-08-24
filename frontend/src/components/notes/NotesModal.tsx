"use client";

/**
 * "Log a call or note" — ported from the prototype's notes modal.
 *
 * 560px, three kind chips, a textarea, Cancel / Log it, then everything logged
 * against this bid newest-first. The subtitle names what it will be saved
 * against, because a note whose subject is ambiguous is a note nobody finds
 * again.
 *
 * Opened from three places, all of which the prototype has: the top bar, the
 * estimate action bar, and any ledger row's "Log a call" — the last of which
 * pre-fills the reference with that item's mark.
 */

import { useState } from "react";
import {
  NOTE_KINDS,
  noteKindStyle,
  noteWhen,
  useLogNote,
  useNotes,
  type NoteKind,
} from "@/lib/notes";

export function NotesModal({
  projectId,
  reference,
  initialKind = "GC_CALL",
  onClose,
  onLogged,
}: {
  projectId: string;
  /** What this is about — an opening mark, a price book, the bid itself. */
  reference: string;
  initialKind?: NoteKind;
  onClose: () => void;
  onLogged?: (kind: NoteKind) => void;
}) {
  const { data: notes } = useNotes(projectId);
  const log = useLogNote(projectId);

  const [kind, setKind] = useState<NoteKind>(initialKind);
  const [text, setText] = useState("");

  function submit() {
    if (!text.trim()) return;
    log.mutate(
      { project: projectId, kind, body: text.trim(), ref: reference },
      {
        onSuccess: () => {
          setText("");
          onLogged?.(kind);
        },
      },
    );
  }

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 60, display: "grid", placeItems: "center", padding: "40px" }}>
      <div
        onClick={onClose}
        style={{ position: "absolute", inset: 0, background: "rgba(6,6,12,0.6)", animation: "fadein 170ms ease" }}
      ></div>

      <div style={{ position: "relative", width: "560px", maxHeight: "100%", display: "flex", flexDirection: "column", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "18px", boxShadow: "var(--app-sh-3)", animation: "popin 220ms cubic-bezier(0.32,0.72,0,1)", fontFamily: "var(--app-font)", color: "var(--app-tx)", overflow: "hidden" }}>
        <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: "11px", padding: "16px 18px", borderBottom: "1px solid var(--app-line)" }}>
          <span style={{ display: "grid", placeItems: "center", width: "34px", height: "34px", borderRadius: "10px", background: "rgba(34,211,238,0.16)" }}>
            <i className="ph-duotone ph-phone-call" style={{ fontSize: "18px", color: "#22d3ee" }}></i>
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: "block", fontSize: "15px", fontWeight: 700 }}>Log a call or note</span>
            <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)" }}>
              Saved against {reference} · travels with the estimate
            </span>
          </span>
          <button
            onClick={onClose}
            className="hv-114a69"
            style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "9px", background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx-3)", cursor: "pointer" }}
          >
            <i className="ph-duotone ph-x" style={{ fontSize: "15px" }}></i>
          </button>
        </div>

        <div style={{ flexShrink: 0, padding: "14px 18px 16px", borderBottom: "1px solid var(--app-line)" }}>
          <div style={{ display: "flex", gap: "5px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "4px" }}>
            {NOTE_KINDS.map((k) => {
              const on = kind === k.value;
              return (
                <button
                  key={k.value}
                  onClick={() => setKind(k.value)}
                  style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: "7px", background: on ? "var(--app-tx)" : "transparent", border: 0, color: on ? "var(--app-bg-2)" : "var(--app-tx)", borderRadius: "8px", padding: "8px 6px", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap", transition: "all 170ms cubic-bezier(0.32,0.72,0,1)" }}
                >
                  <i className={k.icon} style={{ fontSize: "16px" }}></i>
                  {k.label}
                </button>
              );
            })}
          </div>

          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              kind === "INTERNAL"
                ? "What should the next person to open this bid know?"
                : "What was said, and what it changes."
            }
            style={{ width: "100%", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "10px", padding: "10px 12px", fontFamily: "var(--app-font)", fontSize: "13px", color: "var(--app-tx)", outline: "none", marginTop: "10px", minHeight: "92px", resize: "vertical", lineHeight: 1.6, boxSizing: "border-box" }}
          />

          <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
            <button
              onClick={onClose}
              className="hv-114a69"
              style={{ flex: 1, background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", borderRadius: "10px", padding: "10px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}
            >
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={!text.trim() || log.isPending}
              style={{ flex: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", background: "var(--app-accent)", color: "#fff", border: 0, borderRadius: "10px", padding: "10px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: 700, cursor: text.trim() ? "pointer" : "not-allowed", opacity: text.trim() ? 1 : 0.55, transition: "opacity 160ms ease" }}
            >
              <i className="ph-duotone ph-paper-plane-tilt" style={{ fontSize: "16px" }}></i>
              {log.isPending ? "Logging…" : "Log it"}
            </button>
          </div>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden", padding: "14px 18px 20px" }}>
          {(notes ?? []).map((n) => {
            const style = noteKindStyle(n.kind ?? "");
            return (
              <div key={n.id} style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "13px", padding: "13px 14px", marginBottom: "10px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "9px", background: style.bg }}>
                    <i className={style.icon} style={{ fontSize: "16px", color: style.fg }}></i>
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: "block", fontSize: "13px", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {n.who || n.created_by_name || "—"}
                    </span>
                    <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {n.org || n.ref || "—"}
                    </span>
                  </span>
                  <span style={{ flexShrink: 0, fontSize: "11px", color: "var(--app-tx-3)", textAlign: "right" }}>
                    {noteWhen(n.created_at)}
                  </span>
                  <span style={{ flexShrink: 0, display: "grid", placeItems: "center", width: "24px", height: "24px", borderRadius: "7px", background: "var(--app-panel-2)", fontSize: "10px", fontWeight: 700, color: "var(--app-tx-2)" }}>
                    {n.created_by_initials}
                  </span>
                </div>
                <div style={{ fontSize: "12.5px", color: "var(--app-tx)", lineHeight: 1.6, marginTop: "9px" }}>{n.body}</div>
                {n.ref ? (
                  <div style={{ fontSize: "11px", color: "var(--app-tx-3)", marginTop: "7px" }}>{n.ref}</div>
                ) : null}
              </div>
            );
          })}

          {!(notes ?? []).length ? (
            <div style={{ padding: "28px 8px", textAlign: "center", fontSize: "12.5px", color: "var(--app-tx-3)", lineHeight: 1.6 }}>
              Nothing logged against this bid yet. Calls are where most of the answers
              arrive, and none of them are in the documents.
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
