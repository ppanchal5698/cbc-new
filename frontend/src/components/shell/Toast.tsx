"use client";

/**
 * The prototype's `flash(title, sub, warm)` — a small confirmation, bottom-left,
 * gone in 2.6 seconds.
 *
 * Nearly every action in the prototype flashes one, and that is the point rather
 * than decoration: confirming a row, keeping one of a duplicate pair and adding a
 * line by hand all change a list the estimator is looking at, and without a
 * confirmation the only feedback is a row quietly changing colour somewhere in a
 * scroll. `warm` marks the ones that added or changed something rather than
 * merely acknowledging it.
 *
 * Deliberately not a notification centre. These are not kept, not counted and
 * not dismissible — anything worth returning to belongs in the queue or the
 * notes, both of which persist.
 */

export interface ToastState {
  title: string;
  sub?: string;
  warm?: boolean;
}

export function Toast({ toast }: { toast: ToastState | null }) {
  if (!toast) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        left: "18px",
        bottom: "18px",
        zIndex: 80,
        display: "flex",
        alignItems: "center",
        gap: "11px",
        minWidth: "260px",
        maxWidth: "420px",
        background: "var(--app-bg-2)",
        border: `1px solid ${toast.warm ? "var(--app-neg-line)" : "var(--app-accent-line)"}`,
        borderRadius: "13px",
        boxShadow: "var(--app-sh-3)",
        padding: "12px 15px",
        animation: "popin 220ms cubic-bezier(0.32,0.72,0,1)",
      }}
    >
      <span
        style={{
          flexShrink: 0,
          width: "9px",
          height: "9px",
          borderRadius: "50%",
          background: toast.warm ? "var(--app-neg)" : "var(--app-accent)",
          boxShadow: `0 0 0 3px ${toast.warm ? "var(--app-neg-soft)" : "var(--app-accent-soft)"}`,
        }}
      ></span>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "block", fontSize: "13px", fontWeight: 700, letterSpacing: "-0.01em" }}>
          {toast.title}
        </span>
        {toast.sub ? (
          <span style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginTop: "2px", lineHeight: 1.5 }}>
            {toast.sub}
          </span>
        ) : null}
      </span>
    </div>
  );
}
