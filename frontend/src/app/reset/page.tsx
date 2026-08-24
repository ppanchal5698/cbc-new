"use client";

/**
 * Password reset — both halves on one route.
 *
 * With `uid` and `token` in the query string this is the confirm step, reached
 * from the emailed link; without them it is the request step.
 *
 * The request step **always** reports success. Whether an address has an account
 * is not something an anonymous caller gets to learn, and the API is built that
 * way deliberately — a page that said "no such user" would undo it.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { AuthCard, FIELD, LABEL, PRIMARY } from "@/components/auth/AuthCard";

export default function ResetPage() {
  return (
    <Suspense fallback={<AuthCard kicker="Password" title="Loading…">{null}</AuthCard>}>
      <Reset />
    </Suspense>
  );
}

function Reset() {
  const params = useSearchParams();
  const uid = params.get("uid");
  const token = params.get("token");
  return uid && token ? <Confirm uid={uid} token={token} /> : <Request />;
}

function Request() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    // Failures are swallowed on purpose: the answer must not depend on whether
    // the address exists, and that includes the answer when something breaks.
    await apiFetch("/api/auth/password-reset/", {
      method: "POST",
      body: JSON.stringify({ email }),
    }).catch(() => undefined);
    setState("sent");
  }

  if (state === "sent") {
    return (
      <AuthCard kicker="Password" title="Check your email">
        <p style={{ fontSize: "13px", color: "var(--app-tx-2)", lineHeight: 1.65, margin: "0 0 18px" }}>
          If <strong style={{ color: "var(--app-tx)" }}>{email}</strong> has an account, a reset link
          is on its way. The link expires, so use it soon.
        </p>
        <Link href="/login" style={{ ...PRIMARY, display: "block", textAlign: "center", textDecoration: "none" }}>
          Back to sign in
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard kicker="Password" title="Reset your password">
      <form onSubmit={submit}>
        <label style={LABEL}>Work email</label>
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} style={FIELD} />
        <button type="submit" disabled={state === "sending"} className="hv-5e4ad0" style={{ ...PRIMARY, marginTop: "20px" }}>
          {state === "sending" ? "Sending…" : "Send the link"}
        </button>
        <div style={{ marginTop: "16px", fontSize: "12.5px", color: "var(--app-tx-3)" }}>
          <Link href="/login" style={{ color: "var(--app-accent)" }}>
            Back to sign in
          </Link>
        </div>
      </form>
    </AuthCard>
  );
}

function Confirm({ uid, token }: { uid: string; token: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    setError(null);
    try {
      await apiFetch("/api/auth/password-reset/confirm/", {
        method: "POST",
        body: JSON.stringify({ uid, token, new_password: password }),
      });
      setState("done");
      setTimeout(() => router.replace("/login"), 1600);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setState("idle");
    }
  }

  if (state === "done") {
    return (
      <AuthCard kicker="Password" title="Changed">
        <p style={{ fontSize: "13px", color: "var(--app-tx-2)", lineHeight: 1.65 }}>
          Taking you to sign in…
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard kicker="Password" title="Choose a new one">
      <form onSubmit={submit}>
        <label style={LABEL}>New password</label>
        <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} style={FIELD} />

        {error ? (
          <div role="alert" style={{ marginTop: "14px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "10px 12px", fontSize: "12.5px", lineHeight: 1.55 }}>
            {error}
          </div>
        ) : null}

        <button type="submit" disabled={state === "sending"} className="hv-5e4ad0" style={{ ...PRIMARY, marginTop: "20px" }}>
          {state === "sending" ? "Saving…" : "Set the password"}
        </button>
      </form>
    </AuthCard>
  );
}
