"use client";

/**
 * The frame the signed-out pages share.
 *
 * The sign-in screen carries the full Ops-Hub splash; signup and password reset
 * are errands, not arrivals, so they get the same type and palette in a plain
 * centred card rather than a second landing page.
 */

import Link from "next/link";

export function AuthCard({
  kicker,
  title,
  children,
}: {
  kicker: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ height: "100vh", display: "grid", placeItems: "center", background: "var(--app-bg)", padding: "24px", overflowY: "auto" }}>
      <div style={{ width: "100%", maxWidth: "380px" }}>
        <Link href="/login" style={{ display: "flex", alignItems: "baseline", gap: "10px", textDecoration: "none", marginBottom: "26px" }}>
          <span style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "17px", letterSpacing: "-0.01em", color: "var(--app-tx)" }}>
            Hamilton Parker
          </span>
          <span style={{ fontSize: "10.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
            Ops-Hub
          </span>
        </Link>

        <div style={{ fontSize: "11px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>{kicker}</div>
        <h1 style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "30px", letterSpacing: "-0.02em", margin: "8px 0 22px", color: "var(--app-tx)" }}>
          {title}
        </h1>

        {children}
      </div>
    </div>
  );
}

export const LABEL: React.CSSProperties = {
  display: "block",
  fontSize: "12px",
  color: "var(--app-tx-2)",
  marginBottom: "6px",
};

export const FIELD: React.CSSProperties = {
  width: "100%",
  background: "var(--app-panel)",
  border: "1px solid var(--app-line)",
  borderRadius: "10px",
  padding: "10px 12px",
  fontFamily: "var(--app-font)",
  fontSize: "13.5px",
  color: "var(--app-tx)",
  outline: "none",
  boxSizing: "border-box",
};

export const PRIMARY: React.CSSProperties = {
  width: "100%",
  background: "var(--app-accent)",
  color: "#fff",
  border: "0",
  borderRadius: "10px",
  padding: "11px 14px",
  fontFamily: "var(--app-font)",
  fontSize: "14px",
  cursor: "pointer",
  transition: "background 140ms ease",
};
