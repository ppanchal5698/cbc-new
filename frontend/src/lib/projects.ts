"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type { Project } from "./schema";

/** The board's filter chips. `Mine` and the three below are handled server-side. */
export const BOARD_FILTERS = ["All", "Mine", "In flight", "Sent", "Closed"] as const;
export type BoardFilter = (typeof BOARD_FILTERS)[number];

function query(filter: BoardFilter): string {
  const params = new URLSearchParams({ ordering: "due_date", page_size: "200" });
  if (filter !== "All") params.set("board_filter", filter);
  return params.toString();
}

export function useProjects(filter: BoardFilter = "All") {
  return useQuery({
    queryKey: ["projects", filter],
    queryFn: () => apiFetch<Paginated<Project>>(`/api/projects/?${query(filter)}`),
    select: (page) => page.results,
  });
}

/**
 * Header totals for the current filter.
 *
 * A separate call rather than a sum of the page: the page is 200 rows and the
 * header claims to describe every bid, so summing what happens to be loaded
 * would be wrong exactly when the board grows enough to matter.
 */
export function useBoardSummary(filter: BoardFilter = "All") {
  return useQuery({
    queryKey: ["projects", "summary", filter],
    queryFn: () => apiFetch<{ jobs: number; value: string }>(`/api/projects/summary/?${query(filter)}`),
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: ["projects", id],
    queryFn: () => apiFetch<Project>(`/api/projects/${id}/`),
    enabled: Boolean(id),
  });
}

/** Groups the board by brand, preserving the order the rows arrived in. */
export function groupByBrand(projects: Project[]) {
  const groups = new Map<string, Project[]>();
  for (const p of projects) {
    const brand = p.brand?.trim() || "Unassigned";
    (groups.get(brand) ?? groups.set(brand, []).get(brand)!).push(p);
  }
  return [...groups.entries()].map(([brand, rows]) => ({
    brand,
    rows,
    flags: rows.reduce((a, r) => a + (r.flag_count ?? 0), 0),
    value: rows.reduce((a, r) => a + Number(r.quoted_value ?? 0), 0),
  }));
}
