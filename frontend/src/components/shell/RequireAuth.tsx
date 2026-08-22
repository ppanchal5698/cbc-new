"use client";

/**
 * Sends anonymous visitors to /login.
 *
 * This is a convenience, not a security boundary — every endpoint behind it is
 * already `IsAuthenticated` by DRF default, so a client that skipped this check
 * would still be answered with 401s. The boundary is Django's (§11.2).
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useMe } from "@/lib/session";

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: me, isPending, error } = useMe();

  useEffect(() => {
    if (!isPending && me === null) router.replace("/login");
  }, [isPending, me, router]);

  if (isPending || me === null) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh", color: "var(--app-tx-3)", fontSize: "13px" }}>
        {isPending ? "Loading…" : "Redirecting to sign in…"}
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh", padding: "40px", textAlign: "center" }}>
        <div style={{ maxWidth: "420px" }}>
          <div style={{ fontFamily: "var(--app-font-h)", fontWeight: 600, fontSize: "20px" }}>The API did not answer</div>
          <div style={{ marginTop: "8px", fontSize: "13px", color: "var(--app-tx-2)", lineHeight: 1.6 }}>
            {(error as Error).message}
          </div>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
