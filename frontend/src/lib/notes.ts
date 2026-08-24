"use client";

/**
 * Calls and notes against a bid.
 *
 * §1.6 phase 5 — judgment, reuse and RFIs — happens almost entirely on the phone.
 * The GC asks for the FRP scope broken out; the architect concedes the schedule
 * and the elevation disagree and raises an RFI. None of it touches email, and
 * until now none of it touched the bid file either.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type { Note } from "./schema";

export const NOTE_KINDS = [
  { value: "GC_CALL", label: "GC call", icon: "ph-duotone ph-phone-call" },
  { value: "ARCHITECT_CALL", label: "Architect call", icon: "ph-duotone ph-compass-tool" },
  { value: "INTERNAL", label: "Internal note", icon: "ph-duotone ph-note-pencil" },
] as const;

export type NoteKind = (typeof NOTE_KINDS)[number]["value"];

export const noteKindLabel = (kind: string): string =>
  NOTE_KINDS.find((k) => k.value === kind)?.label ?? kind;

/** How each kind reads in the list — the icon chip beside a logged note. */
export function noteKindStyle(kind: string): { icon: string; fg: string; bg: string } {
  switch (kind) {
    case "GC_CALL":
      return { icon: "ph-duotone ph-phone-call", fg: "#22d3ee", bg: "rgba(34,211,238,0.16)" };
    case "ARCHITECT_CALL":
      return { icon: "ph-duotone ph-compass-tool", fg: "var(--app-warn)", bg: "rgba(251,191,36,0.14)" };
    default:
      return { icon: "ph-duotone ph-note-pencil", fg: "var(--app-accent)", bg: "var(--app-accent-soft)" };
  }
}

export function useNotes(projectId: string | undefined) {
  return useQuery({
    queryKey: ["notes", projectId],
    queryFn: () =>
      apiFetch<Paginated<Note>>(`/api/notes/?project=${projectId}&page_size=100`),
    select: (page) => page.results,
    enabled: Boolean(projectId),
  });
}

export interface NewNote {
  project: string;
  kind: NoteKind;
  body: string;
  ref?: string;
  who?: string;
  org?: string;
}

export function useLogNote(projectId: string | undefined) {
  const qc = useQueryClient();
  return useMutation<Note, Error, NewNote>({
    mutationFn: (body) =>
      apiFetch<Note>("/api/notes/", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", projectId] }),
  });
}

/** `11 Aug · 9:12 AM` — how the prototype stamps a note. */
export function noteWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const day = d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${day} · ${time}`;
}
