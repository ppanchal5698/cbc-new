"use client";

/**
 * Bid board — ported from the "bid board" section of the Ops-Hub prototype.
 *
 * Grouped by brand, with the programme header, the filter chips and the eight
 * columns the design specifies. Status, Value, Version and Flags are **derived**
 * server-side (see `backend/api/projects/board.py`) rather than stored, so what
 * the board says about a bid cannot drift from what the pipeline is doing to it.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { brandInitials, chip, dayMonth, money0, plural, statusStyle } from "@/lib/format";
import {
  BOARD_FILTERS,
  groupByBrand,
  useBoardSummary,
  useProjects,
  type BoardFilter,
} from "@/lib/projects";
import type { Project } from "@/lib/schema";

const COLUMNS = "minmax(200px,1.6fr) minmax(150px,1fr) 90px 104px 52px 104px 62px 128px";

export default function BoardPage() {
  return (
    <RequireAuth>
      <AppShell crumbs={[{ label: "Bid board" }]}>
        <Board />
      </AppShell>
    </RequireAuth>
  );
}

function Board() {
  const [filter, setFilter] = useState<BoardFilter>("All");
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const { data: projects, isPending, error } = useProjects(filter);
  const { data: summary } = useBoardSummary(filter);

  const groups = groupByBrand(projects ?? []);

  return (
    <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "flex", flexDirection: "column", overflow: "hidden", padding: "14px 16px" }}>
      <div style={{ flexShrink: "0", display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "20px", padding: "0 2px 14px" }}>
        <div>
          <div style={{ fontSize: "26px", fontWeight: "800", letterSpacing: "-0.025em" }}>Bid board</div>
          <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "3px" }}>
            {summary ? `${plural(summary.jobs, "job")} · ${money0(summary.value)} quoted value` : "Loading…"} · grouped by brand
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "9px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "5px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "4px" }}>
            {BOARD_FILTERS.map((f) => {
              const c = chip(f === filter);
              return (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  style={{ display: "flex", alignItems: "center", gap: "6px", background: c.bg, border: "0", color: c.fg, borderRadius: "8px", padding: "6px 11px", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: "600", cursor: "pointer", whiteSpace: "nowrap", transition: "all 180ms cubic-bezier(0.32,0.72,0,1)" }}
                >
                  {f}
                  {f === filter ? <span style={{ fontSize: "11px", color: c.numFg }}>{projects?.length ?? ""}</span> : null}
                </button>
              );
            })}
          </div>
          <Link
            href="/board/new"
            className="hv-8bebbc"
            style={{ display: "flex", alignItems: "center", gap: "7px", background: "linear-gradient(135deg,#818cf8,#22d3ee)", color: "#0a0a12", border: "0", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: "700", cursor: "pointer", textDecoration: "none", transition: "opacity 160ms ease" }}
          >
            <i className="ph-duotone ph-plus" style={{ fontSize: "15px" }}></i>New estimate
          </Link>
        </div>
      </div>

      <div style={{ flex: "1", minHeight: "0", overflowY: "auto", overflowX: "hidden", padding: "2px" }}>
        {error ? <Notice text={(error as Error).message} tone="neg" /> : null}
        {!error && isPending ? <Notice text="Loading the board…" /> : null}
        {!error && !isPending && groups.length === 0 ? (
          <Notice text={filter === "All" ? "No bids yet. Start one with New estimate." : `Nothing under ${filter}.`} />
        ) : null}

        {groups.map((g) => {
          const open = !collapsed.includes(g.brand);
          return (
            <div key={g.brand} style={{ background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", marginBottom: "12px", overflow: "hidden" }}>
              <button
                onClick={() => setCollapsed((c) => (open ? [...c, g.brand] : c.filter((x) => x !== g.brand)))}
                className="hv-be10ad"
                style={{ width: "100%", display: "grid", gridTemplateColumns: "22px 40px minmax(0,1fr) 104px 120px 110px", gap: "14px", alignItems: "center", textAlign: "left", background: "transparent", border: "0", padding: "14px 18px", fontFamily: "var(--app-font)", color: "var(--app-tx)", cursor: "pointer", transition: "background 160ms ease" }}
              >
                <i className={open ? "ph-duotone ph-caret-down" : "ph-duotone ph-caret-right"} style={{ fontSize: "16px", color: "var(--app-tx-3)" }}></i>
                <span style={{ display: "grid", placeItems: "center", width: "40px", height: "40px", borderRadius: "12px", background: "var(--app-accent-soft)", color: "var(--app-accent)", fontSize: "13px", fontWeight: "800", letterSpacing: "0.02em" }}>
                  {brandInitials(g.brand)}
                </span>
                <span style={{ minWidth: "0" }}>
                  <span style={{ display: "block", fontSize: "16px", fontWeight: "700", letterSpacing: "-0.01em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.brand}</span>
                  <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "1px" }}>{plural(g.rows.length, "bid")} in this programme</span>
                </span>
                <span style={{ fontSize: "11.5px", fontWeight: "600", color: g.flags > 0 ? "var(--app-warn)" : "var(--app-tx-3)", textAlign: "right" }}>
                  {g.flags === 0 ? "No flags" : `${g.flags} flagged`}
                </span>
                <span style={{ textAlign: "right" }}>
                  <span style={{ display: "block", fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Programme value</span>
                  <span style={{ display: "block", fontSize: "15px", fontWeight: "700", marginTop: "2px" }}>{money0(g.value)}</span>
                </span>
                <span style={{ fontSize: "11.5px", color: "var(--app-tx-3)", textAlign: "right" }}>{plural(g.rows.length, "bid")}</span>
              </button>

              {open ? (
                <div style={{ borderTop: "1px solid var(--app-line)", animation: "fadein 200ms cubic-bezier(0.32,0.72,0,1)" }}>
                  <div style={{ display: "grid", gridTemplateColumns: COLUMNS, gap: "0 12px", padding: "8px 18px 8px 44px", fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
                    <span>Bid</span>
                    <span>Customer</span>
                    <span>Due</span>
                    <span style={{ textAlign: "right" }}>Value</span>
                    <span>Est.</span>
                    <span>Version</span>
                    <span style={{ textAlign: "right" }}>Flags</span>
                    <span>Status</span>
                  </div>
                  {g.rows.map((p) => (
                    <Row key={p.id} project={p} />
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Row({ project }: { project: Project }) {
  const router = useRouter();
  const st = statusStyle(project.board_status ?? "Intake");
  const flags = project.flag_count ?? 0;

  return (
    <button
      onClick={() => router.push(`/estimate/${project.id}`)}
      className="hv-be10ad"
      style={{ width: "100%", display: "grid", gridTemplateColumns: COLUMNS, gap: "0 12px", alignItems: "center", textAlign: "left", background: "transparent", border: "0", borderTop: "1px solid var(--app-line)", borderLeft: "3px solid transparent", padding: "12px 18px 12px 41px", fontFamily: "var(--app-font)", fontSize: "13.5px", color: "var(--app-tx)", cursor: "pointer", transition: "background 160ms ease" }}
    >
      <span style={{ minWidth: "0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontWeight: 500 }}>{project.name}</span>
      <span style={{ minWidth: "0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", color: "var(--app-tx-2)", fontSize: "12.5px" }}>
        {project.general_contractor || "—"}
      </span>
      <span style={{ fontSize: "12.5px", color: flags > 2 ? "var(--app-neg)" : "var(--app-tx-2)" }}>{dayMonth(project.due_date)}</span>
      <span style={{ textAlign: "right", fontWeight: "600" }}>{money0(project.quoted_value)}</span>
      <span style={{ fontSize: "11.5px", color: "var(--app-tx-3)" }}>{project.estimator_initials}</span>
      <span style={{ fontSize: "12px", color: "var(--app-tx-2)" }}>{project.version_label}</span>
      <span style={{ fontSize: "12px", textAlign: "right", color: flags > 2 ? "var(--app-neg)" : "var(--app-tx-3)" }}>{flags === 0 ? "—" : flags}</span>
      <span style={{ fontSize: "11px", fontWeight: "600", color: st.fg, background: st.bg, border: `1px solid ${st.line}`, borderRadius: "8px", padding: "3px 9px", justifySelf: "start", whiteSpace: "nowrap" }}>
        {project.board_status}
      </span>
    </button>
  );
}

function Notice({ text, tone }: { text: string; tone?: "neg" }) {
  return (
    <div
      style={{
        background: tone === "neg" ? "var(--app-neg-soft)" : "var(--app-panel)",
        border: `1px solid ${tone === "neg" ? "var(--app-neg-line)" : "var(--app-line)"}`,
        color: tone === "neg" ? "var(--app-neg)" : "var(--app-tx-2)",
        borderRadius: "14px",
        padding: "18px 20px",
        fontSize: "13px",
      }}
    >
      {text}
    </div>
  );
}
