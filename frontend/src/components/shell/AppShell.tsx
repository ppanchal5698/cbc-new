"use client";

/**
 * The application shell — top bar, rail, and the four-row grid — ported from the
 * "application shell" and "rail" sections of the Ops-Hub prototype.
 *
 * Controls the prototype drew but that belong to a later phase render in place
 * and **disabled**, with a title saying so. Deleting them would change the
 * layout the design specifies; leaving them live would be a lie about what works.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useChrome } from "@/app/providers";
import { initialsOf, useLogout, useMe, type Profile } from "@/lib/session";

export interface Crumb {
  label: string;
  href?: string;
}

const NAV = [
  { href: "/", label: "Dashboard", icon: "ph-duotone ph-house" },
  { href: "/board", label: "Bid board", icon: "ph-duotone ph-squares-four" },
  { href: "/catalog", label: "Product catalog", icon: "ph-duotone ph-package" },
  { href: "/books", label: "Price books", icon: "ph-duotone ph-books" },
] as const;

const NOT_YET = (phase: string) => `Not wired yet — ${phase}.`;

export function AppShell({
  crumbs,
  progress,
  actionBar,
  children,
}: {
  crumbs: Crumb[];
  /** The bid-progress stepper. Only the estimate workspace has one. */
  progress?: React.ReactNode;
  actionBar?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { data: me } = useMe();

  return (
    <div
      style={{
        height: "100vh",
        display: "grid",
        gridTemplateColumns: "216px minmax(0,1fr)",
        gridTemplateRows: "54px auto minmax(0,1fr) auto",
      }}
    >
      <TopBar crumbs={crumbs} me={me ?? null} />
      <Rail me={me ?? null} />

      <div style={{ gridColumn: "2", gridRow: "2", minWidth: 0 }}>{progress}</div>

      <div
        style={{
          gridColumn: "2",
          gridRow: "3",
          position: "relative",
          minWidth: 0,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        {children}
      </div>

      <div style={{ gridColumn: "2", gridRow: "4", minWidth: 0 }}>{actionBar}</div>
    </div>
  );
}

function TopBar({ crumbs, me }: { crumbs: Crumb[]; me: Profile | null }) {
  const { toggleTheme, theme } = useChrome();
  const router = useRouter();
  const logout = useLogout();

  const trail: Crumb[] = [{ label: "Workspace", href: "/" }, { label: "/" }, ...crumbs];

  return (
    <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: "18px", padding: "0 18px", background: "var(--app-bg-2)", borderBottom: "1px solid var(--app-line)" }}>
      <div style={{ flex: "1", minWidth: "0", display: "flex", alignItems: "center", gap: "9px", fontSize: "13px", color: "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden" }}>
        {trail.map((c, i) => {
          const last = i === trail.length - 1;
          const style = { color: last ? "var(--app-tx)" : "var(--app-tx-3)", fontWeight: last ? 600 : 400 };
          return c.href && !last ? (
            <Link key={i} href={c.href} style={{ ...style, cursor: "pointer", textDecoration: "none" }}>
              {c.label}
            </Link>
          ) : (
            <span key={i} style={{ ...style, cursor: "default" }}>{c.label}</span>
          );
        })}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "9px", width: "340px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "10px", padding: "8px 11px" }}>
        <i className="ph-duotone ph-magnifying-glass" style={{ fontSize: "16px", color: "var(--app-tx-3)" }}></i>
        <input
          disabled
          title={NOT_YET("the command palette lands with the estimate workspace")}
          placeholder="Ask or run a command…"
          style={{ flex: "1", minWidth: "0", border: "0", outline: "none", background: "transparent", fontFamily: "var(--app-font)", fontSize: "13px", color: "var(--app-tx)" }}
        />
        <span style={{ fontSize: "10.5px", fontWeight: "600", color: "var(--app-tx-3)", background: "var(--app-panel-2)", borderRadius: "6px", padding: "2px 6px", whiteSpace: "nowrap" }}>Ctrl + K</span>
      </div>

      <button
        disabled
        title={NOT_YET("calls and notes are their own phase")}
        className="hv-5fd9a4"
        style={{ position: "relative", display: "flex", alignItems: "center", gap: "7px", height: "34px", padding: "0 12px", borderRadius: "10px", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: "600", cursor: "not-allowed", whiteSpace: "nowrap", transition: "all 160ms cubic-bezier(0.32,0.72,0,1)" }}
      >
        <i className="ph-duotone ph-phone-call" style={{ fontSize: "16px" }}></i>Calls &amp; notes
      </button>

      <button
        onClick={toggleTheme}
        title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        className="hv-5fd9a4"
        style={{ display: "grid", placeItems: "center", width: "34px", height: "34px", borderRadius: "10px", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", cursor: "pointer", transition: "all 150ms ease" }}
      >
        <i className={theme === "dark" ? "ph-duotone ph-sun" : "ph-duotone ph-moon"} style={{ fontSize: "18px" }}></i>
      </button>

      <div style={{ display: "flex", alignItems: "center", gap: "10px", paddingLeft: "6px", borderLeft: "1px solid var(--app-line)" }}>
        <span style={{ position: "relative", display: "grid", placeItems: "center", width: "34px", height: "34px", borderRadius: "50%", background: "var(--app-accent)", color: "#fff", fontSize: "12px", fontWeight: "700", letterSpacing: "0.02em" }}>
          {me ? initialsOf(me) : "··"}
          <span style={{ position: "absolute", bottom: "0", right: "0", width: "9px", height: "9px", borderRadius: "50%", background: "var(--app-pos)", border: "2px solid var(--app-bg-2)" }}></span>
        </span>
        <span style={{ whiteSpace: "nowrap" }}>
          <span style={{ display: "block", fontSize: "13px", fontWeight: "600" }}>{me?.full_name || me?.email || "—"}</span>
          <span style={{ display: "block", fontSize: "11px", color: "var(--app-pos)" }}>Online</span>
        </span>
        <button
          onClick={() => logout.mutate(undefined, { onSuccess: () => router.replace("/login") })}
          className="hv-1a63cc"
          style={{ background: "transparent", border: "0", color: "var(--app-tx-3)", fontFamily: "var(--app-font)", fontSize: "12px", cursor: "pointer", padding: "0 4px" }}
        >
          Sign out
        </button>
      </div>
    </div>
  );
}

function Rail({ me }: { me: Profile | null }) {
  const pathname = usePathname();
  const { focus, setFocus } = useChrome();

  return (
    <div style={{ gridRow: "2 / 5", minWidth: "0", display: "flex", flexDirection: "column", borderRight: "1px solid var(--app-line)", background: "var(--app-bg-2)", overflow: "hidden" }}>
      <div style={{ flexShrink: "0", display: "flex", alignItems: "center", gap: "10px", padding: "16px 18px 14px" }}>
        <span style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "9px", background: "linear-gradient(140deg,#818cf8,#22d3ee)" }}>
          <i className="ph-duotone ph-diamonds-four" style={{ fontSize: "17px", color: "#0a0a12" }}></i>
        </span>
        <span style={{ fontFamily: "var(--app-font-h)", fontWeight: "800", fontSize: "15px", letterSpacing: "0.02em" }}>OPS·HUB</span>
      </div>

      <div style={{ flex: "1", minHeight: "0", overflowY: "auto", overflowX: "hidden", padding: "0 10px 10px" }}>
        {NAV.map((n) => {
          const on = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              className="hv-be10ad"
              style={{ width: "100%", display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto", gap: "11px", alignItems: "center", textAlign: "left", background: on ? "var(--app-accent-soft)" : "transparent", border: "0", borderRadius: "10px", padding: "9px 11px", marginBottom: "2px", fontFamily: "var(--app-font)", fontSize: "13.5px", color: on ? "var(--app-tx)" : "var(--app-tx-2)", fontWeight: on ? 700 : 500, cursor: "pointer", textDecoration: "none", transition: "background 150ms ease" }}
            >
              <i className={n.icon} style={{ fontSize: "18px", color: on ? "var(--app-accent)" : "var(--app-tx-3)" }}></i>
              <span style={{ minWidth: "0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{n.label}</span>
            </Link>
          );
        })}
      </div>

      <div style={{ flexShrink: "0", margin: "0 10px 12px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "14px", padding: "12px 13px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ position: "relative", display: "grid", placeItems: "center", width: "32px", height: "32px", borderRadius: "50%", background: "var(--app-accent)", color: "#fff", fontSize: "11.5px", fontWeight: "700" }}>
            {me ? initialsOf(me) : "··"}
            <span style={{ position: "absolute", bottom: "0", right: "0", width: "9px", height: "9px", borderRadius: "50%", background: "var(--app-pos)", border: "2px solid var(--app-panel)" }}></span>
          </span>
          <span style={{ minWidth: "0" }}>
            <span style={{ display: "block", fontSize: "13px", fontWeight: "700", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{me?.full_name || "—"}</span>
            <span style={{ display: "block", fontSize: "11px", color: "var(--app-tx-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{me?.email ?? ""}</span>
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", marginTop: "12px" }}>
          <span style={{ fontSize: "12.5px", fontWeight: "600" }}>Focus mode</span>
          <button
            onClick={() => setFocus(!focus)}
            aria-pressed={focus}
            style={{ width: "36px", height: "20px", borderRadius: "10px", background: focus ? "var(--app-accent)" : "var(--app-panel-2)", border: "0", padding: "2px", cursor: "pointer", display: "flex", alignItems: "center", transition: "background 180ms ease" }}
          >
            <span style={{ width: "16px", height: "16px", borderRadius: "50%", background: "#fff", transform: focus ? "translateX(16px)" : "translateX(0)", transition: "transform 180ms ease", boxShadow: "var(--app-sh-1)" }}></span>
          </button>
        </div>
        <div style={{ fontSize: "11px", color: "var(--app-tx-3)", marginTop: "5px" }}>
          {focus ? "On — notifications held" : "Off — notifications on"}
        </div>
        <div style={{ height: "4px", borderRadius: "4px", background: "var(--app-panel-2)", marginTop: "8px", overflow: "hidden" }}>
          <div style={{ height: "100%", width: focus ? "68%" : "0%", background: "linear-gradient(90deg,#818cf8,#22d3ee)", transition: "width 280ms ease" }}></div>
        </div>
      </div>
    </div>
  );
}
