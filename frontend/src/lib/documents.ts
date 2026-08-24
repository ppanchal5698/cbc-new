"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type { Document, DocumentManifest, PipelineJob } from "./schema";

export function useDocuments(projectId: string | undefined) {
  return useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => apiFetch<Paginated<Document>>(`/api/documents/?project=${projectId}&ordering=created_at`),
    select: (page) => page.results,
    enabled: Boolean(projectId),
    // While anything is still being read, follow it. The pipeline runs in
    // seconds on a triaged bid set, not minutes (§4.4), so this stops quickly.
    refetchInterval: (query) => {
      const docs = query.state.data?.results ?? [];
      return docs.some((d) => ["UPLOADED", "READY_FOR_PROCESSING", "PROCESSING"].includes(d.status ?? ""))
        ? 4000
        : false;
    },
  });
}

export function usePipelineJobs(documentId: string | undefined) {
  return useQuery({
    queryKey: ["pipeline-jobs", documentId],
    queryFn: () => apiFetch<PipelineJob[]>(`/api/documents/${documentId}/pipeline-jobs/`),
    enabled: Boolean(documentId),
  });
}

export function useManifest(documentId: string | undefined, skippedOnly = false) {
  return useQuery({
    queryKey: ["manifest", documentId, skippedOnly],
    queryFn: () =>
      apiFetch<Paginated<DocumentManifest>>(
        `/api/documents/${documentId}/manifest/?page_size=500${skippedOnly ? "&skipped_only=true" : ""}`,
      ),
    select: (page) => page.results,
    enabled: Boolean(documentId),
  });
}

export interface UploadArgs {
  file: File;
  role?: string;
  readyForProcessing?: boolean;
}

/**
 * The verified intake path.
 *
 * The API checks magic bytes and the checksum before anything reaches the
 * Object-Locked source bucket, and returns a *specific* reason when it refuses.
 * That reason is passed straight through — a generic "upload failed" throws away
 * the only useful half of the answer.
 */
export function useUploadDocument(projectId: string) {
  const qc = useQueryClient();
  return useMutation<Document, Error, UploadArgs>({
    mutationFn: ({ file, role = "BID_SET", readyForProcessing = true }) => {
      const body = new FormData();
      body.append("file", file);
      body.append("role", role);
      body.append("ready_for_processing", String(readyForProcessing));
      return apiFetch<Document>(`/api/projects/${projectId}/documents/`, { method: "POST", body });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents", projectId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** How a document's status reads in the intake table, and in what colour. */
export function documentState(status: string): { label: string; fg: string } {
  switch (status) {
    case "UPLOADED":
      return { label: "Staged", fg: "var(--app-tx-2)" };
    case "READY_FOR_PROCESSING":
      return { label: "Queued", fg: "var(--app-tx-2)" };
    case "PROCESSING":
      return { label: "Reading", fg: "var(--app-accent)" };
    case "PROCESSED":
      return { label: "Read", fg: "var(--app-accent)" };
    case "FAILED":
      return { label: "Failed", fg: "var(--app-neg)" };
    case "QUARANTINED":
      return { label: "Quarantined", fg: "var(--app-neg)" };
    default:
      return { label: status, fg: "var(--app-tx-2)" };
  }
}

export const ROLE_LABELS: Record<string, string> = {
  BID_SET: "Bid set · drawings and schedules",
  ADDENDUM: "Addendum",
  SPEC: "Specification section",
  RFP: "Request for proposal",
  OTHER: "Other",
};


/**
 * Read a page triage decided to skip (§4.3 Tier 5).
 *
 * §4.3 calls this "a required feature, not a nice-to-have", and Risk R12 is the
 * reason: triage is the largest cost win in the system and it introduces exactly
 * one new failure mode — a schedule the classifier did not recognise. A false
 * positive costs $0.015; a false negative costs a missing opening.
 *
 * The endpoint also writes a `feedback` row, because a forced read is the
 * clearest possible statement that the classifier was wrong about a page — which
 * is what improves the Tier 1-3 anchors over time. The UI must not write a
 * second one.
 */
export function useForceRead(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation<DocumentManifest, Error, { pageId: string; reason?: string }>({
    mutationFn: ({ pageId, reason }) =>
      apiFetch<DocumentManifest>(`/api/manifest/${pageId}/force-read/`, {
        method: "POST",
        body: JSON.stringify({ ocr_route: "TEXTRACT_TABLES", reason: reason ?? "" }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["manifest", documentId] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

/** Which pages of an addendum changed, and which were reused for free (§4.7). */
export function usePageDiffs(documentId: string | undefined) {
  return useQuery({
    queryKey: ["page-diffs", documentId],
    queryFn: () =>
      apiFetch<{ page_number: number; status: string }[]>(`/api/documents/${documentId}/page-diffs/`),
    enabled: Boolean(documentId),
  });
}
