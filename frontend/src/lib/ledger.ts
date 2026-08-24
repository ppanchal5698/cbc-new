"use client";

/**
 * The line-item ledger — Stage 2's data layer.
 *
 * A bid set is not only doors. The same extraction pass reads grab bars off a
 * fixture schedule, mirrors off a restroom plan and FRP trim off a finish
 * legend, and an estimator triages all of it in one list. So a row here is a
 * *line item* first and an opening second.
 *
 * `source_kind` is what the list is sorted by in an estimator's head: read
 * cleanly, read twice, needs me, or I typed it. Four states, four actions.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type { Opening } from "./schema";

export const LEDGER_FILTERS = [
  { key: "all", label: "All", icon: "ph-duotone ph-list", source: null },
  { key: "review", label: "Needs a look", icon: "ph-duotone ph-warning-circle", source: "REVIEW" },
  { key: "dup", label: "Duplicates", icon: "ph-duotone ph-copy", source: "DUPLICATE" },
  { key: "manual", label: "By hand", icon: "ph-duotone ph-pencil-line", source: "MANUAL" },
  { key: "extracted", label: "Clear", icon: "ph-duotone ph-check-circle", source: "EXTRACTED" },
] as const;

export type LedgerFilter = (typeof LEDGER_FILTERS)[number]["key"];

export function useLedger(projectId: string | undefined) {
  return useQuery({
    queryKey: ["ledger", projectId],
    queryFn: () =>
      apiFetch<Paginated<Opening>>(`/api/openings/?project=${projectId}&page_size=500`),
    select: (page) => page.results,
    enabled: Boolean(projectId),
  });
}

function useLedgerInvalidator(projectId: string | undefined) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["ledger", projectId] });
    // A confirmed or removed item moves the bid's flag count on the board, and
    // its quote lines are built from these rows.
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["quotes", projectId] });
  };
}

/** The six fields the prototype's edit grid exposes. Nothing else is editable. */
export interface ItemPatch {
  door_number?: string | null;
  description?: string;
  size_raw?: string | null;
  quantity?: string | null;
  csi_division?: string;
  hardware_group?: string | null;
}

export function useUpdateItem(projectId: string | undefined) {
  const invalidate = useLedgerInvalidator(projectId);
  return useMutation<Opening, Error, { id: string; patch: ItemPatch }>({
    mutationFn: ({ id, patch }) =>
      apiFetch<Opening>(`/api/openings/${id}/`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: invalidate,
  });
}

export function useAddItem(projectId: string | undefined) {
  const invalidate = useLedgerInvalidator(projectId);
  return useMutation<Opening, Error, Partial<Opening> & { project: string }>({
    mutationFn: (body) =>
      apiFetch<Opening>("/api/openings/", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: invalidate,
  });
}

export function useRemoveItem(projectId: string | undefined) {
  const invalidate = useLedgerInvalidator(projectId);
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiFetch<void>(`/api/openings/${id}/`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

/** One action per verb, each a POST the API already knows how to log. */
function useItemAction<TArgs>(
  projectId: string | undefined,
  path: (args: TArgs) => string,
  body?: (args: TArgs) => unknown,
) {
  const invalidate = useLedgerInvalidator(projectId);
  return useMutation<unknown, Error, TArgs>({
    mutationFn: (args) =>
      apiFetch(path(args), {
        method: "POST",
        ...(body ? { body: JSON.stringify(body(args)) } : {}),
      }),
    onSuccess: invalidate,
  });
}

export const useConfirmItem = (projectId: string | undefined) =>
  useItemAction<string>(projectId, (id) => `/api/openings/${id}/confirm/`);

export const useKeepOne = (projectId: string | undefined) =>
  useItemAction<string>(projectId, (id) => `/api/openings/${id}/keep-one/`);

export const useKeepBoth = (projectId: string | undefined) =>
  useItemAction<string>(projectId, (id) => `/api/openings/${id}/keep-both/`);

export const useConfirmAll = (projectId: string | undefined) =>
  useItemAction<string>(projectId, () => "/api/openings/confirm-all/", (project) => ({ project }));

export const useBulkConfirm = (projectId: string | undefined) =>
  useItemAction<string[]>(projectId, () => "/api/openings/bulk-confirm/", (ids) => ({ ids }));

export const useBulkRemove = (projectId: string | undefined) =>
  useItemAction<string[]>(projectId, () => "/api/openings/bulk-remove/", (ids) => ({ ids }));

/** Read a document again — §4.3 Tier 5, the "read page 47 anyway" of a whole set. */
export function useReprocess(projectId: string | undefined) {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (documentId) =>
      apiFetch(`/api/documents/${documentId}/reprocess/`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents", projectId] });
      qc.invalidateQueries({ queryKey: ["ledger", projectId] });
    },
  });
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

export interface ItemStyle {
  label: string;
  fg: string;
  bg: string;
  line: string;
  bar: string;
  icon: string;
  headline: string;
}

/**
 * How a row reads, from its `source_kind` alone.
 *
 * The headline is the sentence the expanded row opens with, and it is written to
 * answer the estimator's actual question — *why is this in front of me?* — rather
 * than to restate the label they can already see.
 */
export function itemStyle(item: Opening): ItemStyle {
  switch (item.source_kind) {
    case "MANUAL":
      return {
        label: "By hand",
        fg: "var(--app-neg)",
        bg: "var(--app-neg-soft)",
        line: "var(--app-neg-line)",
        bar: "var(--app-neg)",
        icon: "ph-duotone ph-pencil-line",
        headline: "You added this by hand",
      };
    case "DUPLICATE":
      return {
        label: "Possible duplicate",
        fg: "#f472b6",
        bg: "rgba(244,114,182,0.14)",
        line: "rgba(244,114,182,0.4)",
        bar: "#f472b6",
        icon: "ph-duotone ph-copy",
        headline: "Read twice from two documents",
      };
    case "REVIEW":
      return {
        label: "Needs a look",
        fg: "var(--app-warn)",
        bg: "rgba(251,191,36,0.14)",
        line: "rgba(251,191,36,0.4)",
        bar: "var(--app-warn)",
        icon: "ph-duotone ph-warning-circle",
        headline: "Worth a look before pricing",
      };
    default:
      return {
        label: "Clear",
        fg: "var(--app-accent)",
        bg: "var(--app-accent-soft)",
        line: "var(--app-accent-line)",
        bar: "var(--app-accent)",
        icon: "ph-duotone ph-check-circle",
        headline: "Read cleanly from the sheet",
      };
  }
}

/** The one-click action a row offers, which differs by what is wrong with it. */
export function quickAction(item: Opening): { label: string; icon: string } {
  if (item.source_kind === "DUPLICATE") {
    return { label: "Keep one", icon: "ph-duotone ph-copy-simple" };
  }
  if (item.source_kind === "REVIEW") {
    return { label: "Looks right", icon: "ph-duotone ph-check" };
  }
  return { label: itemStyle(item).label, icon: itemStyle(item).icon };
}

/** `A-601 · Row 4`, or the honest alternative for something typed in. */
export function origin(item: Opening): string {
  if (item.source_kind === "MANUAL") return "Written in by hand";
  return [item.sheet_label, item.cell_label].filter(Boolean).join(" · ") || "—";
}

/**
 * The zero-tolerance fields, for the expanded detail panel.
 *
 * They are off the grid — the prototype's row has no room and CBC's estimators
 * work from mark and description — but §5.8 is unambiguous that a dropped rating
 * or handing is a code-compliance failure, so they are never off the record.
 * "Not stated" is a finding here, not a blank.
 */
export function zeroTolerance(item: Opening): { k: string; v: string; warn: boolean }[] {
  return [
    {
      k: "Fire rating",
      v: item.fire_rating_absent
        ? "Not stated"
        : item.fire_rating_raw || (item.fire_rating_minutes ? `${item.fire_rating_minutes} min` : "Not read"),
      warn: Boolean(item.fire_rating_absent) || !item.fire_rating_minutes,
    },
    {
      k: "Handing",
      v: item.handing_absent ? "Not stated" : item.handing || "Not read",
      warn: Boolean(item.handing_absent) || !item.handing,
    },
    {
      k: "Finish",
      v: item.finish_us_code
        ? `${item.finish_us_code} · ${item.finish_bhma_code}`
        : item.finish_raw || "Not read",
      warn: !item.finish_raw && !item.finish_us_code,
    },
  ];
}
