/**
 * Presentation helpers shared by the board, the quote and the proposal.
 *
 * The colour maps are the prototype's `statusStyle` and `chip`, lifted rather
 * than re-derived so the board keeps the palette the design specifies.
 */

/** `$46,200` — whole dollars, the board's and the programme header's format. */
export function money0(n: number | string | null | undefined): string {
  const v = typeof n === "string" ? Number(n) : (n ?? 0);
  return "$" + Math.round(Number.isFinite(v) ? v : 0).toLocaleString("en-US");
}

/** `$1,284.50` — line-level money, where the cents are the point. */
export function money2(n: number | string | null | undefined): string {
  const v = typeof n === "string" ? Number(n) : (n ?? 0);
  return (Number.isFinite(v) ? v : 0).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}

/** `3 Aug` — the board's due column. */
export function dayMonth(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export interface Swatch {
  fg: string;
  bg: string;
  line: string;
}

/** The status pill's colours, from the prototype's `statusStyle`. */
export function statusStyle(status: string): Swatch {
  if (status === "Won") return { fg: "var(--app-accent)", bg: "var(--app-accent-soft)", line: "var(--app-accent-line)" };
  if (status === "Lost") return { fg: "var(--app-tx-2)", bg: "transparent", line: "var(--app-line)" };
  if (status === "Sent") return { fg: "var(--app-tx)", bg: "var(--app-line)", line: "var(--app-line)" };
  if (status === "Awaiting vendor" || status === "Review")
    return { fg: "var(--app-neg)", bg: "var(--app-neg-soft)", line: "var(--app-neg-line)" };
  return { fg: "var(--app-accent)", bg: "var(--app-accent-soft)", line: "var(--app-accent-line)" };
}

/** A selected/unselected filter chip, from the prototype's `chip`. */
export function chip(on: boolean) {
  return {
    bg: on ? "var(--app-tx)" : "transparent",
    line: on ? "var(--app-tx)" : "var(--app-line)",
    fg: on ? "var(--app-bg-2)" : "var(--app-tx)",
    numFg: "var(--app-tx-3)",
  };
}

/** `Burger King` -> `BK`; `Long John Silver's` -> `LJS`. */
export function brandInitials(brand: string): string {
  return brand
    .replace(/[^A-Za-z ]/g, "")
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0].toUpperCase())
    .join("")
    .slice(0, 3);
}

export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
