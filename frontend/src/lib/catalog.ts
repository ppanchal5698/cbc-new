"use client";

/**
 * The reference library and the price books (FR-3, §7.5).
 *
 * FR-3 is explicit that this library is **independent of any single job file** —
 * it is the fix for the Excel-workbook-per-job status quo, where a part number
 * lived in whichever spreadsheet last used it.
 *
 * Every pricing table here carries an `effective_date`, and vendor multipliers
 * additionally carry a `source_sheet_version`. That is not bookkeeping: NFR-3
 * requires a quote line to be traceable to the exact tier *and* sheet that
 * priced it, so both belong on screen and not only in an audit table.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, type Paginated } from "./api";
import type {
  CatalogItem,
  FinishCode,
  MarginBand,
  TaxRate,
  ThroatDepth,
  VendorMultiplier,
} from "./schema";

export interface CatalogFilters {
  search?: string;
  division?: string | null;
  stockOnly?: boolean;
}

export function useCatalogItems(filters: CatalogFilters = {}) {
  const params = new URLSearchParams({ page_size: "300", ordering: "vendor" });
  if (filters.search) params.set("search", filters.search);
  if (filters.division) params.set("csi_division", filters.division);
  if (filters.stockOnly) params.set("is_stock", "true");
  const query = params.toString();

  return useQuery({
    queryKey: ["catalog-items", query],
    queryFn: () => apiFetch<Paginated<CatalogItem>>(`/api/catalog-items/?${query}`),
    select: (page) => page.results,
  });
}

/** One generic list hook for the five reference tables — they differ only by path. */
function useReference<T>(path: string, ordering: string) {
  return useQuery({
    queryKey: ["reference", path],
    queryFn: () => apiFetch<Paginated<T>>(`/api/${path}/?page_size=300&ordering=${ordering}`),
    select: (page) => page.results,
  });
}

export const useMarginBands = () => useReference<MarginBand>("margin-bands", "product_type_band");
export const useVendorMultipliers = () => useReference<VendorMultiplier>("vendor-multipliers", "vendor_name");
export const useFinishCodes = () => useReference<FinishCode>("finish-codes", "us_code");
export const useThroatDepths = () => useReference<ThroatDepth>("throat-depths", "throat_depth_inches");
export const useTaxRates = () => useReference<TaxRate>("tax-rates", "jurisdiction");

/**
 * Update one reference row.
 *
 * Writes are ADMIN-only server-side; the UI hides the controls rather than
 * letting an estimator discover the 403. Reference data drives money on every
 * quote, and Risk R5 is that it goes stale with nobody named to own it — so an
 * edit here is deliberately a small, visible act rather than an inline one.
 */
export function useUpdateReference(path: string) {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { id: string; patch: Record<string, unknown> }>({
    mutationFn: ({ id, patch }) =>
      apiFetch(`/api/${path}/${id}/`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reference", path] }),
  });
}

/** `08` → `Division 08 · Openings`. */
export function divisionLabel(division: string | null | undefined): string {
  switch (division) {
    case "08":
      return "Openings";
    case "09":
      return "Finishes";
    case "10":
      return "Specialties";
    default:
      return division || "—";
  }
}

export const BAND_LABELS: Record<string, string> = {
  COMMODITY: "Commodity",
  RESTROOM_PARTITIONS: "Restroom partitions",
  SPECIALTY: "Specialty",
  CUSTOM_FABRICATED: "Custom fabricated",
};

/**
 * How a catalogue item reads in the availability column.
 *
 * `is_stock` is the top-10 list NR-13 builds on: stock items are the ones the
 * system prices automatically, and everything beyond is the estimator's long
 * tail by design rather than by failure.
 */
export function availability(item: CatalogItem): { label: string; fg: string } {
  if (!item.is_active) return { label: "Withdrawn", fg: "var(--app-tx-3)" };
  if (item.is_stock) return { label: "Stocked", fg: "var(--app-pos)" };
  return { label: "Special order", fg: "var(--app-tx-2)" };
}
