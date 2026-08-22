"use client";

/**
 * Stage 2 · Extraction and entry — ported from the "stage 2 · extraction & entry"
 * section of the Ops-Hub prototype.
 *
 * Two adaptations, both because the prototype's rows are mock data and the real
 * ones carry provenance:
 *
 *  - The expanded row's six-cell edit grid keeps its exact layout, but its cells
 *    are the six fields an opening actually holds — mark, size, handing, fire
 *    rating, finish, hardware set. Each one is a `field_provenance` row, so each
 *    edit goes through the override endpoint and each has its own citation.
 *  - Qty has no value at this stage. An opening is one location on a schedule;
 *    quantity is a quote-line fact, and inventing one here would put a number in
 *    front of an estimator that nothing in the document supports.
 */

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { EstimateShell } from "@/components/estimate/EstimateShell";
import { SheetViewer } from "@/components/estimate/SheetViewer";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { useDocuments } from "@/lib/documents";
import { plural } from "@/lib/format";
import {
  openingStatus,
  sizeLabel,
  useOpenings,
  useOverrideField,
  useProvenance,
  useSourceRegion,
} from "@/lib/openings";
import { useProject } from "@/lib/projects";
import type { FieldProvenanceGrid, Opening } from "@/lib/schema";

const HEAD = "30px 40px 44px minmax(150px,1fr) 74px 44px 76px 128px";
const ROW = "40px 44px minmax(150px,1fr) 74px 44px 76px";

type Filter = "all" | "review" | "clear";

export default function LinesPage() {
  return (
    <RequireAuth>
      <Lines />
    </RequireAuth>
  );
}

function Lines() {
  const { id } = useParams<{ id: string }>();
  const { data: project } = useProject(id);
  const { data: documents } = useDocuments(id);
  const { data: openings, isPending } = useOpenings(id);

  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(true);
  const [activeDocument, setActiveDocument] = useState<string | undefined>();
  const [tracing, setTracing] = useState<string | null>(null);

  const { data: region } = useSourceRegion(tracing ?? undefined);

  const rows = openings ?? [];
  const needsReview = rows.filter((o) => o.review_state === "FLAGGED" || o.review_state === "REJECTED");
  const shown = useMemo(() => {
    if (filter === "review") return needsReview;
    if (filter === "clear") return rows.filter((o) => !needsReview.includes(o));
    return rows;
  }, [filter, rows, needsReview]);

  const docId = activeDocument ?? documents?.[0]?.id;

  return (
    <EstimateShell
      project={project}
      stage={2}
      subs={{
        1: `${plural(documents?.length ?? 0, "document")}`,
        2: needsReview.length ? `${needsReview.length} to check` : rows.length ? "All clear" : "Nothing yet",
        3: "—",
        4: "—",
      }}
      hint={
        needsReview.length
          ? `${needsReview.length} items need a look. Open one, check it against the sheet, confirm.`
          : "Everything is checked. Pricing is next."
      }
    >
      <div style={{ position: "absolute", inset: "0", minWidth: "0", display: "grid", gridTemplateColumns: sheetOpen ? "minmax(0,1fr) minmax(0,1fr)" : "minmax(0,1fr)", gap: "14px", padding: "14px 16px", overflow: "hidden" }}>
        <div style={{ minWidth: "0", display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)" }}>
          <div style={{ flexShrink: "0", display: "flex", alignItems: "center", gap: "12px", padding: "14px 16px 12px" }}>
            <span style={{ display: "grid", placeItems: "center", width: "36px", height: "36px", borderRadius: "11px", background: "var(--app-accent-soft)" }}>
              <i className="ph-duotone ph-list-checks" style={{ fontSize: "19px", color: "var(--app-accent)" }}></i>
            </span>
            <span style={{ flex: "1", minWidth: "0" }}>
              <span style={{ display: "block", fontSize: "15px", fontWeight: "700", letterSpacing: "-0.01em" }}>Line items</span>
              <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "1px" }}>
                {plural(rows.length, "opening")} ·{" "}
                <span style={{ color: needsReview.length ? "var(--app-warn)" : "var(--app-tx-3)" }}>
                  {needsReview.length ? `${needsReview.length} to check` : "all clear"}
                </span>
              </span>
            </span>
            <button
              onClick={() => setSheetOpen((v) => !v)}
              className="hv-f68886"
              style={{ display: "flex", alignItems: "center", gap: "7px", background: sheetOpen ? "var(--app-accent-soft)" : "var(--app-panel-2)", border: `1px solid ${sheetOpen ? "var(--app-accent-line)" : "var(--app-line)"}`, color: sheetOpen ? "var(--app-accent)" : "var(--app-tx-2)", borderRadius: "9px", padding: "7px 12px", fontFamily: "var(--app-font)", fontSize: "12px", fontWeight: "600", cursor: "pointer", whiteSpace: "nowrap" }}
            >
              <i className="ph-duotone ph-file-pdf" style={{ fontSize: "15px" }}></i>
              {sheetOpen ? "Hide the sheet" : "Show the sheet"}
            </button>
          </div>

          <div style={{ flexShrink: "0", display: "flex", alignItems: "center", gap: "8px", padding: "0 16px 12px" }}>
            <div style={{ flex: "1", display: "flex", alignItems: "center", gap: "5px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "11px", padding: "4px" }}>
              {([
                ["all", "Everything", rows.length],
                ["review", "Needs a look", needsReview.length],
                ["clear", "Clear", rows.length - needsReview.length],
              ] as const).map(([key, label, n]) => {
                const on = filter === key;
                return (
                  <button
                    key={key}
                    onClick={() => setFilter(key)}
                    style={{ display: "flex", alignItems: "center", gap: "6px", background: on ? "var(--app-tx)" : "transparent", border: "0", color: on ? "var(--app-bg-2)" : "var(--app-tx)", borderRadius: "8px", padding: "6px 11px", fontFamily: "var(--app-font)", fontSize: "12.5px", fontWeight: "600", cursor: "pointer", whiteSpace: "nowrap", transition: "all 180ms cubic-bezier(0.32,0.72,0,1)" }}
                  >
                    {label}
                    <span style={{ fontSize: "11px", color: "var(--app-tx-3)" }}>{n}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ flexShrink: "0", display: "grid", gridTemplateColumns: HEAD, gap: "0 10px", padding: "0 16px 8px", fontSize: "10px", fontWeight: "700", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>
            <span></span>
            <span></span>
            <span>Mark</span>
            <span>Description</span>
            <span>Size</span>
            <span>Qty</span>
            <span>HW set</span>
            <span style={{ textAlign: "right" }}>Status</span>
          </div>

          <div style={{ flex: "1", overflowY: "auto", overflowX: "hidden", minHeight: "0", padding: "6px 10px" }}>
            {shown.map((o) => (
              <Row
                key={o.id}
                projectId={id}
                opening={o}
                open={expanded === o.id}
                onToggle={() => setExpanded((x) => (x === o.id ? null : o.id!))}
                onTrace={(provenanceId) => {
                  setTracing(provenanceId);
                  setSheetOpen(true);
                }}
              />
            ))}

            {!shown.length ? (
              <div style={{ padding: "48px 24px", textAlign: "center" }}>
                <div style={{ fontSize: "15px", fontWeight: 700 }}>
                  {isPending ? "Loading…" : rows.length ? "Nothing in this view" : "No openings yet"}
                </div>
                <div style={{ fontSize: "12.5px", color: "var(--app-tx-2)", marginTop: "6px", lineHeight: 1.6, maxWidth: "460px", marginInline: "auto" }}>
                  {rows.length
                    ? "Change the filter to see the rest."
                    : "The schedules have been read, but nothing has been extracted from them yet. Extraction runs on Bedrock, which is not reachable from this machine."}
                </div>
              </div>
            ) : null}
          </div>
        </div>

        {sheetOpen ? (
          <SheetViewer
            documents={documents ?? []}
            activeDocumentId={docId}
            onPickDocument={setActiveDocument}
            region={region ?? null}
            onClose={() => setSheetOpen(false)}
          />
        ) : null}
      </div>
    </EstimateShell>
  );
}

function Row({
  projectId,
  opening,
  open,
  onToggle,
  onTrace,
}: {
  projectId: string;
  opening: Opening;
  open: boolean;
  onToggle: () => void;
  onTrace: (provenanceId: string) => void;
}) {
  const status = openingStatus(opening);
  const flagged = opening.review_state === "FLAGGED" || opening.review_state === "REJECTED";

  return (
    <div className="hv-f68886" style={{ background: open ? "var(--app-bg-2)" : "transparent", border: `1px solid ${open ? "var(--app-accent-line)" : "transparent"}`, borderRadius: "12px", marginBottom: "4px", transition: "all 160ms cubic-bezier(0.32,0.72,0,1)" }}>
      <div style={{ display: "grid", gridTemplateColumns: "30px minmax(0,1fr) 128px", gap: "0", alignItems: "center" }}>
        <span style={{ display: "grid", placeItems: "center", width: "30px", height: "32px", color: "var(--app-tx-3)" }}>
          <i className={open ? "ph-duotone ph-caret-down" : "ph-duotone ph-caret-right"} style={{ fontSize: "15px" }}></i>
        </span>
        <button
          onClick={onToggle}
          style={{ width: "100%", display: "grid", gridTemplateColumns: ROW, gap: "0 10px", alignItems: "center", textAlign: "left", background: "transparent", border: "0", padding: "9px 6px", cursor: "pointer", fontFamily: "var(--app-font)", fontSize: "13px", color: "var(--app-tx)" }}
        >
          <span style={{ display: "grid", placeItems: "center", width: "28px", height: "28px", marginLeft: "4px", borderRadius: "9px", background: status.bg }}>
            <i className={flagged ? "ph-duotone ph-warning" : "ph-duotone ph-door"} style={{ fontSize: "16px", color: status.fg }}></i>
          </span>
          <span style={{ fontWeight: "700" }}>{opening.door_number}</span>
          <span style={{ minWidth: "0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{describe(opening)}</span>
          <span style={{ fontSize: "12px", color: "var(--app-tx-2)" }}>{sizeLabel(opening)}</span>
          <span style={{ fontSize: "12px", color: "var(--app-tx-3)" }} title="Quantity is set on the quote line, not on the schedule.">
            —
          </span>
          <span style={{ fontSize: "12px", color: "var(--app-tx-2)" }}>{opening.hardware_group || "—"}</span>
        </button>
        <span style={{ justifySelf: "end", marginRight: "8px", display: "flex", alignItems: "center", gap: "6px", background: status.bg, border: `1px solid ${status.fg === "var(--app-tx-2)" ? "var(--app-line)" : status.fg}`, color: status.fg, borderRadius: "9px", padding: "5px 10px", fontSize: "11.5px", fontWeight: "600", whiteSpace: "nowrap" }}>
          {status.label}
        </span>
      </div>

      {open ? <Detail projectId={projectId} opening={opening} onTrace={onTrace} /> : null}
    </div>
  );
}

function Detail({
  projectId,
  opening,
  onTrace,
}: {
  projectId: string;
  opening: Opening;
  onTrace: (provenanceId: string) => void;
}) {
  const { data: fields, isPending } = useProvenance(opening.id);
  const override = useOverrideField(projectId);

  return (
    <div style={{ margin: "0 8px 9px", padding: "14px 15px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "11px", animation: "fadein 190ms cubic-bezier(0.32,0.72,0,1)" }}>
      {opening.fire_rating_absent || opening.handing_absent ? (
        <div style={{ display: "flex", alignItems: "flex-start", gap: "11px", marginBottom: "12px", padding: "11px 12px", background: "rgba(251,191,36,0.10)", border: "1px solid rgba(251,191,36,0.35)", borderRadius: "10px" }}>
          <i className="ph-duotone ph-warning" style={{ fontSize: "18px", color: "var(--app-warn)" }}></i>
          <span style={{ minWidth: "0" }}>
            <span style={{ display: "block", fontSize: "12px", fontWeight: "700", color: "var(--app-warn)" }}>
              {[opening.fire_rating_absent ? "No fire rating stated" : null, opening.handing_absent ? "No hand stated" : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
            <span style={{ display: "block", fontSize: "11.5px", color: "var(--app-tx-2)", marginTop: "2px", lineHeight: 1.55 }}>
              Recorded as absent, not guessed. Nothing is carried down from the row above, and neither reaches a quote line without you saying so.
            </span>
          </span>
        </div>
      ) : null}

      {isPending ? (
        <div style={{ fontSize: "12px", color: "var(--app-tx-3)" }}>Loading the citations…</div>
      ) : !fields?.length ? (
        <div style={{ fontSize: "12px", color: "var(--app-tx-3)" }}>No extracted fields on this opening.</div>
      ) : (
        <div style={{ display: "grid", gap: "9px" }}>
          {fields.map((f) => (
            <FieldRow
              key={f.id}
              field={f}
              onTrace={() => onTrace(f.id!)}
              onSave={(value, state) =>
                override.mutate({ id: f.id!, extracted_value: value, review_state: state })
              }
              busy={override.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FieldRow({
  field,
  onTrace,
  onSave,
  busy,
}: {
  field: FieldProvenanceGrid;
  onTrace: () => void;
  onSave: (value: string | null, state: string) => void;
  busy: boolean;
}) {
  const [value, setValue] = useState(field.extracted_value ?? "");
  const dirty = value !== (field.extracted_value ?? "");
  const pct = field.final_confidence != null ? `${Math.round(Number(field.final_confidence) * 100)}%` : "—";
  const flagged = field.review_state === "FLAGGED" || field.review_state === "REJECTED";

  return (
    <div style={{ display: "grid", gridTemplateColumns: "132px minmax(0,1fr) 92px 108px 168px", gap: "9px", alignItems: "center" }}>
      <span style={{ fontSize: "12px", color: "var(--app-tx-3)" }}>{field.field_name?.replaceAll("_", " ")}</span>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        style={{ background: "var(--app-panel)", border: `1px solid ${flagged ? "var(--app-warn)" : "var(--app-line)"}`, borderRadius: "9px", padding: "7px 10px", fontFamily: "var(--app-font)", fontSize: "12.5px", color: "var(--app-tx)", outline: "none", width: "100%" }}
      />
      <span style={{ fontSize: "11.5px", color: flagged ? "var(--app-warn)" : "var(--app-tx-3)", fontVariantNumeric: "tabular-nums" }} title="Composite confidence: the lower of the OCR and model scores, times the completeness penalty.">
        {pct}
      </span>
      <button
        onClick={onTrace}
        className="hv-5fd9a4"
        style={{ display: "flex", alignItems: "center", gap: "6px", background: "var(--app-panel)", border: "1px solid var(--app-line)", color: "var(--app-tx-2)", borderRadius: "9px", padding: "6px 10px", fontFamily: "var(--app-font)", fontSize: "11.5px", fontWeight: "600", cursor: "pointer", whiteSpace: "nowrap" }}
      >
        <i className="ph-duotone ph-file-pdf" style={{ fontSize: "14px" }}></i>See it
      </button>
      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        <button
          onClick={() => onSave(value || null, dirty ? "CORRECTED" : "CONFIRMED")}
          disabled={busy}
          className="hv-05d365"
          style={{ display: "flex", alignItems: "center", gap: "6px", background: "var(--app-accent)", color: "#fff", border: "0", borderRadius: "9px", padding: "7px 11px", fontFamily: "var(--app-font)", fontSize: "11.5px", fontWeight: "700", cursor: busy ? "progress" : "pointer" }}
        >
          <i className="ph-duotone ph-check-circle" style={{ fontSize: "15px" }}></i>
          {dirty ? "Save" : "Confirm"}
        </button>
        {field.rejection_reason ? (
          <span title={field.rejection_reason} style={{ fontSize: "11px", color: "var(--app-neg)", cursor: "help" }}>
            <i className="ph-duotone ph-warning" style={{ fontSize: "15px" }}></i>
          </span>
        ) : null}
      </span>
    </div>
  );
}

/** A one-line description from the fields an opening actually carries. */
function describe(o: Opening): string {
  const bits = [
    o.fire_rating_minutes ? `${o.fire_rating_minutes} min` : o.fire_rating_absent ? "unrated" : null,
    o.handing,
    o.finish_raw,
    o.wall_type,
  ].filter(Boolean);
  return bits.length ? bits.join(" · ") : "—";
}
