"use client";

/**
 * Stage 1 · Intake — ported from the "stage 1 · intake" section of the Ops-Hub
 * prototype: the bid-documents table, the empty state, the three ways in, and
 * the job record beside them.
 */

import { useParams, useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { EstimateShell } from "@/components/estimate/EstimateShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { ApiError } from "@/lib/api";
import { documentState, ROLE_LABELS, useDocuments, useUploadDocument } from "@/lib/documents";
import { dayMonth, plural } from "@/lib/format";
import { useProject } from "@/lib/projects";
import type { Document } from "@/lib/schema";

const COLUMNS = "minmax(0,1fr) 92px 96px 128px";

export default function IntakePage() {
  return (
    <RequireAuth>
      <Intake />
    </RequireAuth>
  );
}

function Intake() {
  const { id } = useParams<{ id: string }>();
  const { data: project } = useProject(id);
  const { data: documents, isPending } = useDocuments(id);
  const upload = useUploadDocument(id);
  const fileInput = useRef<HTMLInputElement>(null);
  const [rejected, setRejected] = useState<string | null>(null);

  const docs = documents ?? [];
  const empty = !isPending && docs.length === 0;
  const pages = docs.reduce((a, d) => a + (d.page_count ?? 0), 0);
  const addenda = docs.filter((d) => d.role === "ADDENDUM");

  function pick(role: string) {
    if (fileInput.current) {
      fileInput.current.dataset.role = role;
      fileInput.current.click();
    }
  }

  function onFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const role = e.target.dataset.role || "BID_SET";
    setRejected(null);
    for (const file of Array.from(e.target.files ?? [])) {
      upload.mutate(
        { file, role },
        { onError: (err) => setRejected(err instanceof ApiError ? err.message : String(err)) },
      );
    }
    e.target.value = "";
  }

  return (
    <EstimateShell
      project={project}
      stage={1}
      subs={{
        1: docs.length ? `${plural(docs.length, "document")}${addenda.length ? ` · addendum ${addenda.length}` : ""}` : "Nothing yet",
        2: "—",
        3: "—",
        4: "—",
      }}
      hint="Everything the GC sent, plus anything you add yourself."
    >
      <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "grid", gridTemplateColumns: "minmax(0,1fr) 340px", overflow: "hidden" }}>
        <div style={{ minWidth: "0", overflowY: "auto", overflowX: "hidden", padding: "24px 32px 32px", borderRight: "1px solid var(--app-line)" }}>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "24px", letterSpacing: "-0.015em" }}>Bid documents</div>
          <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "3px" }}>
            {empty
              ? "Add the bid set to begin — nothing has been uploaded for this estimate yet."
              : `${plural(docs.length, "file")}${pages ? ` · ${plural(pages, "page")}` : ""}${project?.general_contractor ? ` from ${project.general_contractor}` : ""}.`}
          </div>

          {!empty ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: COLUMNS, gap: "0 14px", marginTop: "24px", padding: "9px 6px", borderBottom: "1px solid var(--app-line)", fontSize: "10.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
                <span>File</span>
                <span>Pages</span>
                <span>Received</span>
                <span>State</span>
              </div>
              {docs.map((d) => (
                <Row key={d.id} document={d} />
              ))}
            </>
          ) : (
            <div style={{ display: "grid", placeItems: "center", padding: "64px 20px 40px", textAlign: "center" }}>
              <span style={{ display: "grid", placeItems: "center", width: "66px", height: "66px", borderRadius: "20px", background: "var(--app-accent-soft)", marginBottom: "18px" }}>
                <i className="ph-duotone ph-tray" style={{ fontSize: "32px", color: "var(--app-accent)" }}></i>
              </span>
              <span style={{ fontSize: "20px", fontWeight: "700", letterSpacing: "-0.015em" }}>No documents yet</span>
              <span style={{ fontSize: "13.5px", color: "var(--app-tx-2)", maxWidth: "420px", marginTop: "7px", lineHeight: "1.6" }}>
                Drop the bid set in and the schedules, elevations and specs are read for you. You can also start from a past bid, or type the openings in by hand.
              </span>
            </div>
          )}

          {rejected ? (
            <div role="alert" style={{ marginTop: "18px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "10px 12px", fontSize: "12.5px", lineHeight: 1.55 }}>
              {rejected}
            </div>
          ) : null}

          <input ref={fileInput} type="file" accept="application/pdf" multiple onChange={onFiles} style={{ display: "none" }} />

          <div style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "22px", flexWrap: "wrap" }}>
            <Way icon="ph-duotone ph-upload-simple" label={upload.isPending ? "Uploading…" : "Upload files"} onClick={() => pick("BID_SET")} disabled={upload.isPending} />
            <Way icon="ph-duotone ph-copy-simple" label="Add an addendum" onClick={() => pick("ADDENDUM")} disabled={upload.isPending} />
            <Way icon="ph-duotone ph-clock-counter-clockwise" label="Start from a past bid" title="Prior-quote reuse lands with the quote screens." disabled />
            <Way icon="ph-duotone ph-pencil-line" label="Enter openings by hand" title="The line-items screen is not built yet." disabled tone="neg" />
            <span style={{ fontSize: "11.5px", color: "var(--app-tx-3)" }}>
              Only PDFs, checked by their contents rather than their extension.
            </span>
          </div>
        </div>

        <div style={{ minWidth: "0", overflowY: "auto", overflowX: "hidden", padding: "22px 20px 28px", background: "var(--app-bg-2)" }}>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Job record</div>
          <div style={{ marginTop: "10px" }}>
            {[
              ["Brand", project?.brand],
              ["Customer", project?.general_contractor],
              ["Architect", project?.architect],
              ["Bid due", project?.due_date ? dayMonth(project.due_date) : null],
              ["Arrived", project?.source_channel === "PHONE" ? "By phone" : project?.source_channel === "EMAIL" ? "By email" : "Entered by hand"],
              ["Initiator", project?.initiator_email],
              ["Estimator", project?.estimator_initials],
              ["Status", project?.board_status],
            ].map(([k, v]) => (
              <div key={k as string} style={{ display: "grid", gridTemplateColumns: "92px minmax(0,1fr)", gap: "10px", alignItems: "baseline", padding: "7px 0", borderBottom: "1px solid var(--app-line)" }}>
                <span style={{ fontSize: "12px", color: "var(--app-tx-3)" }}>{k}</span>
                <span style={{ fontSize: "13px", wordBreak: "break-word" }}>{v || "—"}</span>
              </div>
            ))}
          </div>

          {project?.rfp_body_text ? (
            <>
              <div style={{ marginTop: "22px", fontSize: "10.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>The request</div>
              <div style={{ marginTop: "9px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "12px", padding: "12px 13px", fontSize: "12.5px", color: "var(--app-tx)", lineHeight: "1.6", whiteSpace: "pre-wrap" }}>
                {project.rfp_body_text}
              </div>
            </>
          ) : null}

          {addenda.length ? (
            <>
              <div style={{ marginTop: "22px", fontSize: "10.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
                Addendum {addenda.length}
              </div>
              <div style={{ marginTop: "9px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "12px", padding: "12px 13px", fontSize: "12.5px", color: "var(--app-tx)", lineHeight: "1.6" }}>
                {addenda[addenda.length - 1].filename}. Pages that did not change are reused rather than read again — which page changed is on the sheet view.
              </div>
            </>
          ) : null}
        </div>
      </div>
    </EstimateShell>
  );
}

function Row({ document: d }: { document: Document }) {
  const state = documentState(d.status ?? "");
  return (
    <div className="hv-40d530" style={{ display: "grid", gridTemplateColumns: COLUMNS, gap: "0 14px", alignItems: "center", padding: "11px 6px", borderBottom: "1px solid var(--app-line)" }}>
      <span style={{ minWidth: "0" }}>
        <span style={{ display: "block", fontSize: "13.5px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.filename}</span>
        <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)" }}>
          {ROLE_LABELS[d.role ?? "OTHER"] ?? d.role}
          {d.was_repaired ? " · repaired copy" : ""}
          {d.is_encrypted ? " · password protected" : ""}
        </span>
      </span>
      <span style={{ fontSize: "13px", fontVariantNumeric: "tabular-nums", color: "var(--app-tx-2)" }}>{d.page_count ?? "—"}</span>
      <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums", color: "var(--app-tx-2)" }}>{dayMonth(d.created_at)}</span>
      <span style={{ fontSize: "11.5px", color: state.fg }} title={d.status_detail || undefined}>
        {state.label}
      </span>
    </div>
  );
}

function Way({
  icon,
  label,
  onClick,
  disabled,
  title,
  tone,
}: {
  icon: string;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
  tone?: "neg";
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="hv-f68886"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        background: tone === "neg" ? "var(--app-neg-soft)" : "var(--app-panel)",
        border: `1px solid ${tone === "neg" ? "var(--app-neg-line)" : "var(--app-line)"}`,
        color: disabled ? "var(--app-tx-3)" : tone === "neg" ? "var(--app-neg)" : "var(--app-tx)",
        borderRadius: "10px",
        padding: "9px 14px",
        fontFamily: "var(--app-font)",
        fontSize: "13px",
        fontWeight: "600",
        cursor: disabled ? "not-allowed" : "pointer",
        whiteSpace: "nowrap",
        transition: "all 160ms cubic-bezier(0.32,0.72,0,1)",
      }}
    >
      <i className={icon} style={{ fontSize: "16px" }}></i>
      {label}
    </button>
  );
}
