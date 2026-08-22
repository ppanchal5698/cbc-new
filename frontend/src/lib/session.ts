"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, ApiError } from "./api";

/** `ProfileSerializer` — the caller's own profile, never anyone else's. */
export interface Profile {
  id: string;
  email: string;
  full_name: string;
  job_title: string;
  phone: string;
  role: "ADMIN" | "ESTIMATOR";
  is_active: boolean;
  is_staff: boolean;
  date_joined: string;
  last_login: string | null;
}

export const meKey = ["auth", "me"] as const;

/**
 * `data === null` means signed out. A 401 is the normal answer for an anonymous
 * visitor, so it resolves rather than throwing — only a real failure throws.
 */
export function useMe() {
  return useQuery<Profile | null>({
    queryKey: meKey,
    queryFn: async () => {
      try {
        return await apiFetch<Profile>("/api/auth/me/");
      } catch (err) {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) return null;
        throw err;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation<Profile, ApiError, { email: string; password: string }>({
    mutationFn: (body) =>
      apiFetch<Profile>("/api/auth/login/", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (profile) => qc.setQueryData(meKey, profile),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch("/api/auth/logout/", { method: "POST" }),
    // The session is gone server-side; drop every cached answer that was read
    // under it rather than leaving another estimator's board on screen.
    onSuccess: () => {
      qc.setQueryData(meKey, null);
      qc.clear();
    },
  });
}

/** Initials for the avatar chip, e.g. "Rick Gilbert" -> "RG". */
export function initialsOf(profile: Pick<Profile, "full_name" | "email">): string {
  const parts = profile.full_name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return profile.email.slice(0, 2).toUpperCase();
}
