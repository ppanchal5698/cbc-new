"use client";

/**
 * The estimate workspace: the bid-progress stepper and the action bar, ported
 * from the "bid progress" and "action bar" sections of the Ops-Hub prototype.
 *
 * Four stages — Intake, Extraction & entry, Quote, Proposal. `built` gates a
 * stage: an unbuilt one renders in place and disabled rather than linking
 * somewhere that 404s. All four are built.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/shell/AppShell";
import { dayMonth } from "@/lib/format";
import type { Project } from "@/lib/schema";

export type StageKey = 1 | 2 | 3 | 4;

export const STAGES: { key: StageKey; label: string; icon: string; path: string; built: boolean }[] = [
  { key: 1, label: "Intake", icon: "ph-duotone ph-folder-open", path: "", built: true },
  { key: 2, label: "Extraction & entry", icon: "ph-duotone ph-list-checks", path: "/lines", built: true },
  { key: 3, label: "Quote", icon: "ph-duotone ph-calculator", path: "/quote", built: true },
  { key: 4, label: "Proposal", icon: "ph-duotone ph-file-text", path: "/proposal", built: true },
];

export function EstimateShell({
  project,
  stage,
  subs,
  hint,
  children,
}: {
  project: Project | undefined;
  stage: StageKey;
  /** One line under each stage label — counts, totals, state. */
  subs: Record<StageKey, string>;
  /** The action bar's centre line. */
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <AppShell
      crumbs={[
        { label: "Bid board", href: "/board" },
        { label: `${project?.name ?? "Estimate"} · ${STAGES[stage - 1].label}` },
      ]}
      progress={<Progress project={project} stage={stage} subs={subs} />}
      actionBar={<ActionBar project={project} stage={stage} hint={hint} />}
    >
      {children}
    </AppShell>
  );
}

function Progress({
  project,
  stage,
  subs,
}: {
  project: Project | undefined;
  stage: StageKey;
  subs: Record<StageKey, string>;
}) {
  const pct = `${Math.round(((stage - 1) / 3) * 100)}%`;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "18px", padding: "12px 20px 13px", borderBottom: "1px solid var(--app-line)", background: "var(--app-bg-2)" }}>
      <span style={{ flexShrink: "0", width: "250px", minWidth: "0" }}>
        <span style={{ display: "block", fontSize: "13.5px", fontWeight: "700", letterSpacing: "-0.01em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {project?.name ?? "…"}
        </span>
        <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "2px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {project?.brand ? `${project.brand} · ` : ""}bid due {dayMonth(project?.due_date)}
        </span>
      </span>

      <span style={{ flex: "1", minWidth: "0", display: "flex", alignItems: "center", gap: "8px" }}>
        {STAGES.map((s) => {
          const cur = s.key === stage;
          const done = s.key < stage;
          const style: React.CSSProperties = {
            flex: "1",
            minWidth: "0",
            display: "grid",
            gridTemplateColumns: "24px minmax(0,1fr)",
            gap: "9px",
            alignItems: "center",
            textAlign: "left",
            background: cur ? "var(--app-accent-soft)" : "var(--app-panel)",
            border: `1px solid ${cur ? "var(--app-accent-line)" : "var(--app-line)"}`,
            borderRadius: "11px",
            padding: "7px 11px",
            fontFamily: "var(--app-font)",
            textDecoration: "none",
            cursor: s.built ? "pointer" : "not-allowed",
            opacity: s.built ? 1 : 0.55,
            transition: "all 170ms cubic-bezier(0.32,0.72,0,1)",
          };
          const inner = (
            <>
              <span style={{ display: "grid", placeItems: "center", width: "24px", height: "24px", borderRadius: "8px", background: cur ? "var(--app-accent)" : done ? "var(--app-accent-soft)" : "var(--app-panel-2)", color: cur ? "#fff" : done ? "var(--app-accent)" : "var(--app-tx-3)", fontSize: "11px", fontWeight: "700" }}>
                <i className={done ? "ph-duotone ph-check" : s.icon} style={{ fontSize: "14px" }}></i>
              </span>
              <span style={{ minWidth: "0" }}>
                <span style={{ display: "block", fontSize: "12.5px", fontWeight: cur ? 700 : 500, color: cur || done ? "var(--app-tx)" : "var(--app-tx-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {s.label}
                </span>
                <span style={{ display: "block", fontSize: "11px", color: "var(--app-tx-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {subs[s.key]}
                </span>
              </span>
            </>
          );

          return s.built && project ? (
            <Link key={s.key} href={`/estimate/${project.id}${s.path}`} className="hv-f68886" style={style}>
              {inner}
            </Link>
          ) : (
            <span key={s.key} title={s.built ? undefined : `${s.label} is not built yet.`} style={style}>
              {inner}
            </span>
          );
        })}
      </span>

      <span style={{ flexShrink: "0", textAlign: "right" }}>
        <span style={{ display: "block", fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Bid progress</span>
        <span style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "4px" }}>
          <span style={{ width: "120px", height: "5px", borderRadius: "5px", background: "var(--app-panel-2)", overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: pct, background: "linear-gradient(90deg,#818cf8,#22d3ee)", transition: "width 320ms cubic-bezier(0.32,0.72,0,1)" }}></span>
          </span>
          <span style={{ fontSize: "13px", fontWeight: "700" }}>{pct}</span>
        </span>
      </span>
    </div>
  );
}

const BTN: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  background: "var(--app-panel)",
  border: "1px solid var(--app-line)",
  color: "var(--app-tx-2)",
  borderRadius: "10px",
  padding: "9px 15px",
  fontFamily: "var(--app-font)",
  fontSize: "13px",
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
  transition: "all 160ms cubic-bezier(0.32,0.72,0,1)",
};

function ActionBar({ project, stage, hint }: { project: Project | undefined; stage: StageKey; hint: string }) {
  const router = useRouter();
  const next = STAGES[stage];

  return (
    <div style={{ height: "58px", display: "flex", alignItems: "center", gap: "10px", padding: "0 18px", borderTop: "1px solid var(--app-line)", background: "var(--app-bg-2)" }}>
      <button
        onClick={() => (stage === 1 ? router.push("/board") : router.back())}
        className="hv-8bcdf4"
        style={BTN}
      >
        <i className="ph-duotone ph-arrow-left" style={{ fontSize: "16px" }}></i>
        {stage === 1 ? "Bid board" : "Back"}
      </button>

      <button disabled title="Calls and notes are their own phase." className="hv-7d6430" style={{ ...BTN, cursor: "not-allowed" }}>
        <i className="ph-duotone ph-phone-call" style={{ fontSize: "16px" }}></i>Log a call
      </button>

      <div style={{ flex: "1", minWidth: "0", fontSize: "12.5px", color: "var(--app-tx-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {hint}
      </div>

      {/* No "Save draft": every edit in this app is a request that already
          succeeded or failed. A button that saves nothing is worse than none. */}

      {next && project ? (
        next.built ? (
          <Link
            href={`/estimate/${project.id}${next.path}`}
            className="hv-8bebbc"
            style={{ display: "flex", alignItems: "center", gap: "8px", background: "linear-gradient(135deg,#818cf8,#22d3ee)", color: "#0a0a12", border: "0", borderRadius: "10px", padding: "10px 18px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: "700", cursor: "pointer", whiteSpace: "nowrap", textDecoration: "none", transition: "opacity 160ms ease" }}
          >
            {next.label}
            <i className="ph-duotone ph-caret-right" style={{ fontSize: "16px" }}></i>
          </Link>
        ) : (
          <span
            title={`${next.label} is not built yet.`}
            style={{ display: "flex", alignItems: "center", gap: "8px", background: "var(--app-panel-2)", color: "var(--app-tx-3)", border: "1px solid var(--app-line)", borderRadius: "10px", padding: "10px 18px", fontSize: "13px", fontWeight: "700", cursor: "not-allowed", whiteSpace: "nowrap" }}
          >
            {next.label}
            <i className="ph-duotone ph-caret-right" style={{ fontSize: "16px" }}></i>
          </span>
        )
      ) : null}
    </div>
  );
}
