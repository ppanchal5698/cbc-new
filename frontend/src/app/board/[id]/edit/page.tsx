"use client";

/**
 * Edit estimate — the same record `board/new` creates, adapted for update.
 * Copies that page's panel/label/input styles rather than a new visual
 * language; adds only the one field creation doesn't need, `outcome`, since
 * that's the bid-lifecycle state this screen exists to change.
 */

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DateInput } from "@/components/form/DateInput";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { ApiError } from "@/lib/api";
import { useProject, useUpdateProject } from "@/lib/projects";

const INPUT: React.CSSProperties = {
  width: "100%",
  background: "var(--app-bg-2)",
  border: "1px solid var(--app-line)",
  borderRadius: "10px",
  padding: "9px 11px",
  fontFamily: "var(--app-font)",
  fontSize: "13.5px",
  color: "var(--app-tx)",
  outline: "none",
};

const LABEL: React.CSSProperties = {
  display: "block",
  fontSize: "12px",
  color: "var(--app-tx-2)",
  marginBottom: "6px",
};

const EMPTY_FORM = {
  name: "",
  source_channel: "EMAIL",
  initiator_email: "",
  due_date: "",
  brand: "",
  architect: "",
  general_contractor: "",
  rfp_body_text: "",
  outcome: "",
};

export default function EditEstimatePage() {
  return (
    <RequireAuth>
      <EditEstimate />
    </RequireAuth>
  );
}

function EditEstimate() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: project } = useProject(id);
  const update = useUpdateProject(id);
  const dueRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);

  useEffect(() => {
    if (!project) return;
    setForm({
      name: project.name ?? "",
      source_channel: project.source_channel ?? "EMAIL",
      initiator_email: project.initiator_email ?? "",
      due_date: project.due_date ?? "",
      brand: project.brand ?? "",
      architect: project.architect ?? "",
      general_contractor: project.general_contractor ?? "",
      rfp_body_text: project.rfp_body_text ?? "",
      outcome: project.outcome ?? "",
    });
  }, [project]);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const due_date = dueRef.current?.value || form.due_date || null;
      await update.mutateAsync({
        ...form,
        due_date,
        brand: form.brand || null,
        architect: form.architect || null,
        general_contractor: form.general_contractor || null,
        outcome: (form.outcome || null) as "WON" | "LOST" | null,
        source_channel: form.source_channel as "EMAIL" | "MANUAL" | "PHONE",
      });
      router.replace(`/estimate/${id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <AppShell crumbs={[{ label: "Bid board", href: "/board" }, { label: project?.name ?? "Estimate", href: `/estimate/${id}` }, { label: "Edit" }]}>
      <div style={{ position: "absolute", inset: "0", overflowY: "auto", padding: "26px 32px 40px" }}>
        <div style={{ maxWidth: "760px" }}>
          <div style={{ fontSize: "26px", fontWeight: "800", letterSpacing: "-0.025em" }}>Edit estimate</div>
          <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "3px" }}>
            Update the job record.
          </div>

          <form
            onSubmit={submit}
            style={{ marginTop: "20px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", padding: "20px 22px 22px" }}
          >
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.6fr) 150px 170px", gap: "14px" }}>
              <div>
                <label htmlFor="name" style={LABEL}>Job name</label>
                <input id="name" required value={form.name} onChange={set("name")} style={INPUT} />
              </div>
              <div>
                <label htmlFor="channel" style={LABEL}>How it arrived</label>
                <select id="channel" value={form.source_channel} onChange={set("source_channel")} style={INPUT}>
                  <option value="EMAIL">Email</option>
                  <option value="MANUAL">Entered by hand</option>
                  <option value="PHONE">Phoned in</option>
                </select>
              </div>
              <div>
                <label htmlFor="due" style={LABEL}>Bid due</label>
                <DateInput
                  ref={dueRef}
                  id="due"
                  value={form.due_date}
                  onValueChange={(due_date) => setForm((f) => ({ ...f, due_date }))}
                  style={INPUT}
                />
              </div>
            </div>

            <div style={{ marginTop: "14px" }}>
              <label htmlFor="initiator" style={LABEL}>Initiator&apos;s email</label>
              <input id="initiator" type="email" required value={form.initiator_email} onChange={set("initiator_email")} style={INPUT} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: "14px", marginTop: "14px" }}>
              <div>
                <label htmlFor="brand" style={LABEL}>Brand</label>
                <input id="brand" value={form.brand} onChange={set("brand")} style={INPUT} />
              </div>
              <div>
                <label htmlFor="gc" style={LABEL}>General contractor</label>
                <input id="gc" value={form.general_contractor} onChange={set("general_contractor")} style={INPUT} />
              </div>
              <div>
                <label htmlFor="architect" style={LABEL}>Architect</label>
                <input id="architect" value={form.architect} onChange={set("architect")} style={INPUT} />
              </div>
            </div>

            <div style={{ marginTop: "14px" }}>
              <label htmlFor="outcome" style={LABEL}>Outcome</label>
              <select id="outcome" value={form.outcome} onChange={set("outcome")} style={{ ...INPUT, maxWidth: "220px" }}>
                <option value="">Not decided yet</option>
                <option value="WON">Won</option>
                <option value="LOST">Lost</option>
              </select>
            </div>

            <div style={{ marginTop: "14px" }}>
              <label htmlFor="rfp" style={LABEL}>Request text</label>
              <textarea id="rfp" rows={4} value={form.rfp_body_text} onChange={set("rfp_body_text")} style={{ ...INPUT, resize: "vertical" }} />
            </div>

            {error ? (
              <div role="alert" style={{ marginTop: "14px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "9px 12px", fontSize: "12.5px" }}>
                {error}
              </div>
            ) : null}

            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "20px" }}>
              <button
                type="submit"
                disabled={update.isPending}
                className="hv-8bebbc"
                style={{ display: "flex", alignItems: "center", gap: "7px", background: "linear-gradient(135deg,#818cf8,#22d3ee)", color: "#0a0a12", border: "0", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: "700", cursor: update.isPending ? "progress" : "pointer", opacity: update.isPending ? 0.6 : 1 }}
              >
                <i className="ph-duotone ph-check" style={{ fontSize: "15px" }}></i>
                {update.isPending ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                onClick={() => router.push(`/estimate/${id}`)}
                className="hv-b20764"
                style={{ background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx)", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", cursor: "pointer" }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    </AppShell>
  );
}
