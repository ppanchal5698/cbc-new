"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type { FieldProvenanceGrid, Match, Opening } from "./schema";

export function useOpenings(projectId: string | undefined) {
  return useQuery({
    queryKey: ["openings", projectId],
    queryFn: () => apiFetch<Paginated<Opening>>(`/api/openings/?project=${projectId}&page_size=500`),
    select: (page) => page.results,
    enabled: Boolean(projectId),
  });
}

export function useProvenance(openingId: string | undefined) {
  return useQuery({
    queryKey: ["provenance", openingId],
    queryFn: () =>
      apiFetch<Paginated<FieldProvenanceGrid>>(`/api/provenance/?opening=${openingId}&page_size=100`),
    select: (page) => page.results,
    enabled: Boolean(openingId),
  });
}

export function useMatches(openingId: string | undefined) {
  return useQuery({
    queryKey: ["matches", openingId],
    queryFn: () => apiFetch<Match[]>(`/api/openings/${openingId}/matches/`),
    enabled: Boolean(openingId),
  });
}

/**
 * Where one extracted field came from.
 *
 * Polygons arrive as 0-1 page fractions, which map straight onto CSS
 * percentages — the overlay is drawn client-side over a pre-rendered raster, so
 * showing the source costs a CDN GET rather than a PDF crop on the API host
 * (bottleneck B5).
 */
export interface SourceRegion {
  page_number: number;
  raster_url: string | null;
  page_width_pt: number | null;
  page_height_pt: number | null;
  /** Applied at draw time. A rotated sheet drawn without it lands 90° out (§4.5). */
  rotation: number;
  polygons: [number, number][][];
  bbox: { x_min: number | null; y_min: number | null; x_max: number | null; y_max: number | null } | null;
}

export function useSourceRegion(provenanceId: string | undefined) {
  return useQuery({
    queryKey: ["provenance", provenanceId, "source"],
    queryFn: () => apiFetch<SourceRegion>(`/api/provenance/${provenanceId}/source/`),
    enabled: Boolean(provenanceId),
    // The source PDF is immutable and the rasters are written once at ingest.
    staleTime: Infinity,
  });
}

/**
 * Correct or confirm one extracted field.
 *
 * The endpoint writes the FR-13 feedback row inside the same transaction, so the
 * UI must not write one of its own — two rows per edit would double-count the
 * tuning dataset.
 */
export function useOverrideField(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; extracted_value: string | null; review_state: string; reason?: string }) =>
      apiFetch(`/api/provenance/${args.id}/override/`, {
        method: "POST",
        body: JSON.stringify({
          extracted_value: args.extracted_value,
          review_state: args.review_state,
          reason: args.reason ?? "",
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["openings", projectId] });
      qc.invalidateQueries({ queryKey: ["provenance"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

/** `3070` -> `3'-0" x 7'-0"`, from the parsed inches the API already returns. */
export function sizeLabel(o: Pick<Opening, "size_raw" | "width_inches" | "height_inches">): string {
  if (o.width_inches && o.height_inches) {
    const ft = (n: number) => `${Math.floor(n / 12)}'-${n % 12}"`;
    return `${ft(o.width_inches)} × ${ft(o.height_inches)}`;
  }
  return o.size_raw || "—";
}

/**
 * What an opening's row says in the Status column.
 *
 * `fire_rating_absent` and `handing_absent` are deliberately distinct from null:
 * "the schedule does not state it" is a finding, "we have not read it yet" is
 * not, and §5.8 forbids collapsing the two.
 */
export function openingStatus(o: Opening): { label: string; fg: string; bg: string } {
  if (o.review_state === "REJECTED") return { label: "Rejected", fg: "var(--app-neg)", bg: "var(--app-neg-soft)" };
  if (o.review_state === "FLAGGED") return { label: "Needs a look", fg: "var(--app-warn)", bg: "rgba(251,191,36,0.14)" };
  if (o.fire_rating_absent) return { label: "No rating stated", fg: "var(--app-warn)", bg: "rgba(251,191,36,0.14)" };
  if (o.handing_absent) return { label: "No hand stated", fg: "var(--app-warn)", bg: "rgba(251,191,36,0.14)" };
  if (o.review_state === "CONFIRMED") return { label: "Confirmed", fg: "var(--app-accent)", bg: "var(--app-accent-soft)" };
  if (o.review_state === "CORRECTED") return { label: "Corrected", fg: "var(--app-accent)", bg: "var(--app-accent-soft)" };
  return { label: "Read", fg: "var(--app-tx-2)", bg: "var(--app-panel-2)" };
}
