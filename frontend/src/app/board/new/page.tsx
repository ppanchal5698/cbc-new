"use client";

/**
 * New estimate.
 *
 * The prototype jumps straight from "New estimate" to an empty Stage 1, because
 * its bid record is mock data. A real Project needs a name and an initiator
 * before a document can be attached to it — FR-10 routes the finished proposal
 * back to that person, never a group inbox — so this is the one screen the
 * design does not draw. It is built from the prototype's own panel, label and
 * input styles rather than a new visual language, and it collects nothing beyond
 * what the record requires plus the FR-11 reuse keys.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { apiFetch, ApiError } from "@/lib/api";
import type { Project } from "@/lib/schema";
import { useMe } from "@/lib/session";

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

export default function NewEstimatePage() {
  return (
    <RequireAuth>
      <AppShell crumbs={[{ label: "Bid board", href: "/board" }, { label: "New estimate" }]}>
        <NewEstimate />
      </AppShell>
    </RequireAuth>
  );
}

function NewEstimate() {
  const router = useRouter();
  const { data: me } = useMe();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "",
    source_channel: "EMAIL",
    initiator_email: "",
    due_date: "",
    brand: "",
    architect: "",
    general_contractor: "",
    rfp_body_text: "",
  });

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const project = await apiFetch<Project>("/api/projects/", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          due_date: form.due_date || null,
          brand: form.brand || null,
          architect: form.architect || null,
          general_contractor: form.general_contractor || null,
        }),
      });
      router.replace(`/estimate/${project.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div style={{ position: "absolute", inset: "0", overflowY: "auto", padding: "26px 32px 40px" }}>
      <div style={{ maxWidth: "760px" }}>
        <div style={{ fontSize: "26px", fontWeight: "800", letterSpacing: "-0.025em" }}>New estimate</div>
        <div style={{ fontSize: "13px", color: "var(--app-tx-2)", marginTop: "3px" }}>
          Open the record, then add the bid documents. A phoned-in bid starts here too.
        </div>

        <form
          onSubmit={submit}
          style={{ marginTop: "20px", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", padding: "20px 22px 22px" }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.6fr) 150px 170px", gap: "14px" }}>
            <div>
              <label htmlFor="name" style={LABEL}>Job name</label>
              <input id="name" required value={form.name} onChange={set("name")} placeholder="Burger King #2379 — exterior & interior renovation" style={INPUT} />
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
              {/* A native date input: the platform already ships a picker, a
                  keyboard path and a locale, and none of that needs a library. */}
              <input id="due" type="date" value={form.due_date} onChange={set("due_date")} style={INPUT} />
            </div>
          </div>

          <div style={{ marginTop: "14px" }}>
            <label htmlFor="initiator" style={LABEL}>Initiator&apos;s email</label>
            <input
              id="initiator"
              type="email"
              required
              value={form.initiator_email}
              onChange={set("initiator_email")}
              placeholder={me?.email ?? "who asked for this quote"}
              style={INPUT}
            />
            <div style={{ fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "5px", lineHeight: 1.5 }}>
              The finished proposal goes back to this person, never a group inbox.
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: "14px", marginTop: "14px" }}>
            <div>
              <label htmlFor="brand" style={LABEL}>Brand</label>
              <input id="brand" value={form.brand} onChange={set("brand")} placeholder="Burger King" style={INPUT} />
            </div>
            <div>
              <label htmlFor="gc" style={LABEL}>General contractor</label>
              <input id="gc" value={form.general_contractor} onChange={set("general_contractor")} placeholder="Cortlandt Builders LLC" style={INPUT} />
            </div>
            <div>
              <label htmlFor="architect" style={LABEL}>Architect</label>
              <input id="architect" value={form.architect} onChange={set("architect")} placeholder="MKN Associates" style={INPUT} />
            </div>
          </div>
          <div style={{ fontSize: "11.5px", color: "var(--app-tx-3)", marginTop: "6px", lineHeight: 1.5 }}>
            These three are how a past bid is found again when the next one for the same programme arrives.
          </div>

          <div style={{ marginTop: "14px" }}>
            <label htmlFor="rfp" style={LABEL}>Request text</label>
            <textarea
              id="rfp"
              rows={4}
              value={form.rfp_body_text}
              onChange={set("rfp_body_text")}
              placeholder="Paste the email body, or type what was said on the phone."
              style={{ ...INPUT, resize: "vertical" }}
            />
          </div>

          {error ? (
            <div role="alert" style={{ marginTop: "14px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "9px 12px", fontSize: "12.5px" }}>
              {error}
            </div>
          ) : null}

          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "20px" }}>
            <button
              type="submit"
              disabled={busy}
              className="hv-8bebbc"
              style={{ display: "flex", alignItems: "center", gap: "7px", background: "linear-gradient(135deg,#818cf8,#22d3ee)", color: "#0a0a12", border: "0", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", fontWeight: "700", cursor: busy ? "progress" : "pointer", opacity: busy ? 0.6 : 1 }}
            >
              <i className="ph-duotone ph-plus" style={{ fontSize: "15px" }}></i>
              {busy ? "Opening…" : "Open the estimate"}
            </button>
            <button
              type="button"
              onClick={() => router.push("/board")}
              className="hv-b20764"
              style={{ background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx)", borderRadius: "10px", padding: "9px 15px", fontFamily: "var(--app-font)", fontSize: "13px", cursor: "pointer" }}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
