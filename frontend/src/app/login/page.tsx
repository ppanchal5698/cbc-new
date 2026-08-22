"use client";

/**
 * Sign in — ported from the "sign in" section of the Ops-Hub prototype.
 *
 * Layout, type scale and copy are the prototype's. What is wired underneath is
 * the real Django session login, including the state the backend deliberately
 * distinguishes: a *correct* password on an account an admin has not activated
 * is told to wait, and every other failure is told the same thing.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useLogin, useMe } from "@/lib/session";

export default function LoginPage() {
  const router = useRouter();
  const { data: me } = useMe();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");

  useEffect(() => {
    if (me) router.replace("/");
  }, [me, router]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    login.mutate({ email, password: pw }, { onSuccess: () => router.replace("/") });
  }

  return (
    <div style={{ height: "100vh", display: "grid", gridTemplateColumns: "1.15fr 0.85fr" }}>
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", padding: "54px 60px 44px", borderRight: "1px solid var(--app-line)" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "12px" }}>
          <span style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "19px", letterSpacing: "-0.01em" }}>Hamilton Parker</span>
          <span style={{ fontSize: "11.5px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Commercial Building Components</span>
        </div>
        <div style={{ maxWidth: "620px" }}>
          <div style={{ height: "5px", background: "var(--app-tx)" }}></div>
          <div style={{ height: "1px", background: "var(--app-tx)", marginTop: "4px" }}></div>
          <h1 style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "74px", lineHeight: "0.98", letterSpacing: "-0.025em", margin: "26px 0 0" }}>Ops‑Hub</h1>
          <div style={{ fontSize: "22px", lineHeight: "1.4", color: "var(--app-tx)", marginTop: "14px", maxWidth: "520px", textWrap: "pretty" }}>The estimating and pricing desk for CBC — bid documents in, priced proposal out.</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: "26px", marginTop: "46px", maxWidth: "600px" }}>
            <span style={{ fontSize: "13px", color: "var(--app-tx-2)", lineHeight: "1.55" }}><span style={{ display: "block", fontVariantNumeric: "tabular-nums", fontFamily: "var(--app-font-h)", fontSize: "26px", color: "var(--app-tx)" }}>7</span>documents read per bid, schedules and elevations reconciled against addenda</span>
            <span style={{ fontSize: "13px", color: "var(--app-tx-2)", lineHeight: "1.55" }}><span style={{ display: "block", fontVariantNumeric: "tabular-nums", fontFamily: "var(--app-font-h)", fontSize: "26px", color: "var(--app-tx)" }}>14</span>price books and multiplier programs kept current by purchasing</span>
            <span style={{ fontSize: "13px", color: "var(--app-tx-2)", lineHeight: "1.55" }}><span style={{ display: "block", fontVariantNumeric: "tabular-nums", fontFamily: "var(--app-font-h)", fontSize: "26px", color: "var(--app-tx)" }}>4</span>steps from intake to the signed proposal, with every number traceable</span>
          </div>
        </div>
        <div style={{ fontSize: "11.5px", color: "var(--app-tx-3)" }}>Internal system · Estimating department · Columbus, Ohio</div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", padding: "0 60px", background: "var(--app-bg-2)" }}>
        <form onSubmit={submit} style={{ maxWidth: "360px", width: "100%" }}>
          <div style={{ fontSize: "11px", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--app-tx-3)" }}>Sign in</div>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: "600", fontSize: "28px", letterSpacing: "-0.015em", marginTop: "6px" }}>Welcome back</div>

          <div style={{ marginTop: "26px" }}>
            <label htmlFor="email" style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginBottom: "6px" }}>Work email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ width: "100%", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "12px", padding: "10px 12px", fontFamily: "var(--app-font)", fontSize: "14px", color: "var(--app-tx)", outline: "none" }}
            />
          </div>

          <div style={{ marginTop: "16px" }}>
            <label htmlFor="pw" style={{ display: "block", fontSize: "12px", color: "var(--app-tx-2)", marginBottom: "6px" }}>Password</label>
            <input
              id="pw"
              type="password"
              autoComplete="current-password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              style={{ width: "100%", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "12px", padding: "10px 12px", fontFamily: "var(--app-font)", fontSize: "14px", color: "var(--app-tx)", outline: "none" }}
            />
          </div>

          {login.error ? (
            <div
              role="alert"
              style={{ marginTop: "14px", background: "var(--app-neg-soft)", border: "1px solid var(--app-neg-line)", color: "var(--app-neg)", borderRadius: "10px", padding: "9px 12px", fontSize: "12.5px", lineHeight: "1.5" }}
            >
              {login.error.message}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={login.isPending || !email || !pw}
            className="hv-5e4ad0"
            style={{ width: "100%", marginTop: "22px", background: "var(--app-accent)", color: "#fff", border: "0", borderRadius: "10px", padding: "11px 14px", fontFamily: "var(--app-font)", fontSize: "14px", cursor: login.isPending ? "progress" : "pointer", opacity: login.isPending || !email || !pw ? 0.6 : 1, transition: "background 140ms ease" }}
          >
            {login.isPending ? "Signing in…" : "Sign in"}
          </button>

          {/* Cognito is deferred (ADR-0004 / C3): Django auth is the authorisation
              and audit boundary. The button stays in the layout the engineer drew,
              visibly inert, rather than being quietly deleted. */}
          <button
            type="button"
            disabled
            title="Single sign-on is not enabled yet — sign in with your work email."
            style={{ width: "100%", marginTop: "9px", background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx-3)", borderRadius: "10px", padding: "10px 14px", fontFamily: "var(--app-font)", fontSize: "13.5px", cursor: "not-allowed" }}
          >
            Continue with Hamilton Parker SSO
          </button>

          <div style={{ marginTop: "18px", fontSize: "12px", color: "var(--app-tx-3)", lineHeight: "1.6" }}>Signed in as an estimator you see your own bid board. Purchasing and sales see the same jobs with their own columns.</div>
        </form>
      </div>
    </div>
  );
}
