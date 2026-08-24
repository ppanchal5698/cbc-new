"use client";

/**
 * Request access.
 *
 * Signing up creates an account an admin still has to activate — the API is
 * explicit that this is "a request for access, not the granting of it". So the
 * success state says *waiting*, not *welcome*: telling someone they are in when
 * they cannot yet sign in is the kind of small lie that costs a support call.
 *
 * The API deliberately answers the same way whether or not the address already
 * has an account, so this page must never claim otherwise.
 */

import Link from "next/link";
import { useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { AuthCard, FIELD, LABEL, PRIMARY } from "@/components/auth/AuthCard";

export default function SignupPage() {
  const [form, setForm] = useState({ email: "", password: "", full_name: "", job_title: "" });
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    setError(null);
    try {
      await apiFetch("/api/auth/signup/", { method: "POST", body: JSON.stringify(form) });
      setState("sent");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setState("idle");
    }
  }

  if (state === "sent") {
    return (
      <AuthCard kicker="Request received" title="Waiting on an admin">
        <p style={{ fontSize: "13px", color: "var(--app-tx-2)", lineHeight: 1.65, margin: "0 0 18px" }}>
          Your account exists but is not active yet. Someone in the estimating team has to switch it
          on before you can sign in — that is deliberate, not a delay.
        </p>
        <Link href="/login" style={{ ...PRIMARY, display: "block", textAlign: "center", textDecoration: "none" }}>
          Back to sign in
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard kicker="Request access" title="Create an account">
      <form onSubmit={submit}>
        <label style={LABEL}>Work email</label>
        <input
          type="email"
          required
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          style={FIELD}
          placeholder="you@hamiltonparker.com"
        />

        <label style={{ ...LABEL, marginTop: "14px" }}>Full name</label>
        <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} style={FIELD} />

        <label style={{ ...LABEL, marginTop: "14px" }}>Job title</label>
        <input value={form.job_title} onChange={(e) => setForm({ ...form, job_title: e.target.value })} style={FIELD} />

        <label style={{ ...LABEL, marginTop: "14px" }}>Password</label>
        <input
          type="password"
          required
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          style={FIELD}
        />

        {error ? (
          <div role="alert" style={{ marginTop: "14px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "10px 12px", fontSize: "12.5px", lineHeight: 1.55 }}>
            {error}
          </div>
        ) : null}

        <button type="submit" disabled={state === "sending"} className="hv-5e4ad0" style={{ ...PRIMARY, marginTop: "20px" }}>
          {state === "sending" ? "Sending…" : "Request access"}
        </button>

        <div style={{ marginTop: "16px", fontSize: "12.5px", color: "var(--app-tx-3)" }}>
          Already have an account?{" "}
          <Link href="/login" style={{ color: "var(--app-accent)" }}>
            Sign in
          </Link>
        </div>
      </form>
    </AuthCard>
  );
}
