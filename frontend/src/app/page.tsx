"use client";

/**
 * Dashboard — ported from the "home" section of the Ops-Hub prototype.
 *
 * The queue is built from the board's derived status rather than a second
 * endpoint: every row the prototype hand-wrote (needs a look, waiting on
 * extraction, waiting on a vendor, ready to send) is a `board_status` the API
 * already returns.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { dayMonth, plural } from "@/lib/format";
import { useProjects } from "@/lib/projects";
import type { Project } from "@/lib/schema";
import { useMe } from "@/lib/session";

export default function HomePage() {
  return (
    <RequireAuth>
      <AppShell crumbs={[{ label: "Dashboard" }]}>
        <Dashboard />
      </AppShell>
    </RequireAuth>
  );
}

/** How each board status reads in the queue, and what it looks like. */
const QUEUE_KINDS: Record<string, { tag: string; icon: string; fg: string; bg: string; sub: (p: Project) => string }> = {
  Review: {
    tag: "Check",
    icon: "ph-duotone ph-warning-circle",
    fg: "var(--app-warn)",
    bg: "rgba(251,191,36,0.14)",
    sub: (p) => `${plural(p.flag_count ?? 0, "item")} flagged — the schedule and the model disagree`,
  },
  Extracting: {
    tag: "Extracting",
    icon: "ph-duotone ph-scan",
    fg: "var(--app-accent)",
    bg: "var(--app-accent-soft)",
    sub: (p) => `${p.general_contractor || "—"} · reading the bid set`,
  },
  "Awaiting vendor": {
    tag: "Waiting",
    icon: "ph-duotone ph-hourglass-medium",
    fg: "var(--app-tx-2)",
    bg: "var(--app-panel-2)",
    sub: (p) => `${p.general_contractor || "—"} · a line is priced from a vendor quote that has not come back`,
  },
};

const QUEUE_ORDER = ["Review", "Awaiting vendor", "Extracting"];

function Dashboard() {
  const { data: me } = useMe();
  const { data: projects, isPending } = useProjects("All");

  const rows = projects ?? [];
  const queue = rows
    .filter((p) => p.board_status && p.board_status in QUEUE_KINDS)
    .sort((a, b) => QUEUE_ORDER.indexOf(a.board_status!) - QUEUE_ORDER.indexOf(b.board_status!));

  const open = rows.filter((p) => !["Won", "Lost", "Sent"].includes(p.board_status ?? ""));
  const clear = open.filter((p) => (p.flag_count ?? 0) === 0);
  // A real ratio rather than a decorative one: of the bids still in flight, the
  // share with nothing flagged for review.
  const pct = open.length ? Math.round((clear.length / open.length) * 100) : 100;
  const brands = new Set(rows.map((p) => p.brand?.trim() || "Unassigned"));
  const first = (me?.full_name || "").trim().split(/\s+/)[0];

  return (
    <div style={{ position: "absolute", inset: "0", minWidth: "0", overflowY: "auto", overflowX: "hidden", padding: "26px 32px 40px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "24px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontFamily: "var(--app-font-h)", fontWeight: "800", fontSize: "30px", letterSpacing: "-0.025em" }}>
              {greeting()}{first ? `, ${first}` : ""}
            </span>
            <span style={{ fontSize: "24px" }}>👋</span>
          </div>
          <div style={{ fontSize: "14px", color: "var(--app-tx-2)", marginTop: "4px" }}>
            {isPending
              ? "Loading your bids…"
              : `${plural(rows.length, "bid")} across ${plural(brands.size, "brand programme")} · bid documents in, priced proposal out`}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "12px", padding: "11px 14px", boxShadow: "var(--app-sh-1)" }}>
          <i className="ph-duotone ph-buildings" style={{ fontSize: "20px", color: "var(--app-accent)" }}></i>
          <span>
            <span style={{ display: "block", fontSize: "11px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Current workspace</span>
            <span style={{ display: "block", fontSize: "14px", fontWeight: "600", marginTop: "1px" }}>Hamilton Parker · CBC</span>
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 300px", gap: "16px", marginTop: "26px" }}>
        <div style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "9px", padding: "16px 20px 13px" }}>
            <i className="ph-duotone ph-list-checks" style={{ fontSize: "19px", color: "var(--app-accent)" }}></i>
            <span style={{ fontSize: "15px", fontWeight: "700" }}>Your queue</span>
            <span style={{ flex: "1" }}></span>
            <Link href="/board" className="hv-36ec74" style={{ background: "transparent", border: "0", color: "var(--app-accent)", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: "600", cursor: "pointer", padding: "0", textDecoration: "none" }}>
              Open the bid board
            </Link>
          </div>

          {queue.length === 0 ? (
            <div style={{ borderTop: "1px solid var(--app-line)", padding: "22px 20px", fontSize: "13px", color: "var(--app-tx-2)" }}>
              {isPending ? "Loading…" : "Nothing is waiting on you."}
            </div>
          ) : (
            queue.map((p) => <QueueRow key={p.id} project={p} />)
          )}
        </div>

        <div style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", padding: "18px 20px 20px", boxShadow: "var(--app-sh-1)", alignSelf: "start" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "9px" }}>
            <i className="ph-duotone ph-timer" style={{ fontSize: "19px", color: "var(--app-accent)" }}></i>
            <span style={{ fontSize: "15px", fontWeight: "700" }}>Focus</span>
          </div>
          <div style={{ display: "grid", placeItems: "center", marginTop: "18px" }}>
            <span
              style={{
                position: "relative",
                display: "grid",
                placeItems: "center",
                width: "132px",
                height: "132px",
                borderRadius: "50%",
                background: `conic-gradient(var(--app-accent) 0turn ${pct / 100}turn, var(--app-panel-2) ${pct / 100}turn 1turn)`,
              }}
            >
              <span style={{ display: "grid", placeItems: "center", width: "106px", height: "106px", borderRadius: "50%", background: "var(--app-panel)" }}>
                <span style={{ fontSize: "26px", fontWeight: "800", letterSpacing: "-0.02em" }}>{pct}%</span>
                <span style={{ fontSize: "10.5px", color: "var(--app-tx-3)" }}>Clear of flags</span>
              </span>
            </span>
          </div>
          <div style={{ fontSize: "13.5px", fontWeight: "700", marginTop: "16px" }}>
            {open.length === 0 ? "Nothing in flight" : pct === 100 ? "Everything is clear" : "Some bids need a look"}
          </div>
          <div style={{ fontSize: "12.5px", color: "var(--app-tx-2)", marginTop: "4px", lineHeight: "1.55" }}>
            {open.length === 0
              ? "No open bids right now."
              : `${clear.length} of ${plural(open.length, "open bid")} have nothing flagged for review.`}
          </div>
        </div>
      </div>
    </div>
  );
}

function QueueRow({ project }: { project: Project }) {
  const router = useRouter();
  const kind = QUEUE_KINDS[project.board_status!];
  const overdue = project.due_date ? new Date(project.due_date) < new Date() : false;

  return (
    <button
      onClick={() => router.push(`/estimate/${project.id}`)}
      className="hv-be10ad"
      style={{ width: "100%", display: "grid", gridTemplateColumns: "38px minmax(0,1fr) 118px 96px 24px", gap: "14px", alignItems: "center", textAlign: "left", background: "transparent", border: "0", borderTop: "1px solid var(--app-line)", padding: "13px 20px", fontFamily: "var(--app-font)", cursor: "pointer", transition: "background 160ms ease" }}
    >
      <span style={{ display: "grid", placeItems: "center", width: "34px", height: "34px", borderRadius: "10px", background: kind.bg }}>
        <i className={kind.icon} style={{ fontSize: "18px", color: kind.fg }}></i>
      </span>
      <span style={{ minWidth: "0" }}>
        <span style={{ display: "block", fontSize: "13.5px", fontWeight: "600", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{project.name}</span>
        <span style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginTop: "1px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{kind.sub(project)}</span>
      </span>
      <span style={{ fontSize: "11.5px", fontWeight: "600", color: kind.fg, background: kind.bg, borderRadius: "8px", padding: "3px 9px", justifySelf: "start", whiteSpace: "nowrap" }}>{kind.tag}</span>
      <span style={{ fontSize: "12px", color: overdue ? "var(--app-neg)" : "var(--app-tx-2)", textAlign: "right", whiteSpace: "nowrap" }}>
        {project.due_date ? `Due ${dayMonth(project.due_date)}` : "No due date"}
      </span>
      <i className="ph-duotone ph-caret-right" style={{ fontSize: "15px", color: "var(--app-tx-3)" }}></i>
    </button>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}
