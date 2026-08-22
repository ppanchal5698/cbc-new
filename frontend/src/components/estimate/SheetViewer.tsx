"use client";

/**
 * The sheet panel — the right half of Stage 2.
 *
 * The prototype draws a *facsimile* of a door schedule here: a white page with
 * monospace rows it made up. The real system has the page itself — a raster
 * rendered once at ingest and served from the CDN — plus the polygons of the
 * elements a field was read from. So the chrome is the prototype's (header,
 * file chips, zoom, the 56px page strip), and the page area shows the actual
 * sheet with the highlight drawn over it.
 *
 * The overlay is client-side SVG in CSS percentages: the polygons are already
 * 0-1 page fractions, so no server-side cropping is involved at all (B5).
 *
 * Rotation is deliberately NOT applied here. §4.5 warns that a rotated sheet
 * mishandled puts every highlight 90° out — and the handling is upstream:
 * `page.get_pixmap` honours /Rotate when the raster is rendered, and PyMuPDF
 * reports word coordinates in that same rotated page space, which is what
 * normalisation divides by `page.rect`. Both sides are already visual, so
 * rotating again here would be the bug rather than the fix. The field is still
 * carried on the payload so the mismatch is visible if that ever changes.
 */

import { useEffect, useMemo, useState } from "react";
import { useManifest } from "@/lib/documents";
import type { SourceRegion } from "@/lib/openings";
import type { Document, DocumentManifest } from "@/lib/schema";

const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3];

export function SheetViewer({
  documents,
  activeDocumentId,
  onPickDocument,
  region,
  onClose,
}: {
  documents: Document[];
  activeDocumentId: string | undefined;
  onPickDocument: (id: string) => void;
  /** The field being traced, if any. Null shows the page on its own. */
  region: SourceRegion | null;
  onClose: () => void;
}) {
  const { data: pages } = useManifest(activeDocumentId);
  const [zoomIndex, setZoomIndex] = useState(2);
  const [page, setPage] = useState(1);

  // Following a citation moves the viewer to that page.
  useEffect(() => {
    if (region?.page_number) setPage(region.page_number);
  }, [region?.page_number]);

  const current = useMemo(
    () => (pages ?? []).find((p) => p.page_number === page) ?? (pages ?? [])[0],
    [pages, page],
  );
  const zoom = ZOOMS[zoomIndex];
  const showOverlay = region?.page_number === current?.page_number;

  return (
    <div style={{ minWidth: "0", display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--app-panel)", border: "1px solid var(--app-line)", borderRadius: "16px", boxShadow: "var(--app-sh-1)", animation: "fadein 220ms cubic-bezier(0.32,0.72,0,1)" }}>
      <div style={{ flexShrink: "0", display: "flex", alignItems: "center", gap: "11px", padding: "14px 16px 12px", borderBottom: "1px solid var(--app-line)" }}>
        <span style={{ display: "grid", placeItems: "center", width: "34px", height: "34px", borderRadius: "10px", background: "rgba(34,211,238,0.16)" }}>
          <i className="ph-duotone ph-file-pdf" style={{ fontSize: "18px", color: "#22d3ee" }}></i>
        </span>
        <span style={{ flex: "1", minWidth: "0" }}>
          <span style={{ display: "block", fontSize: "14px", fontWeight: "700", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {current ? pageTitle(current) : "No sheet"}
          </span>
          <span style={{ display: "block", fontSize: "11px", color: "var(--app-tx-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {current ? `Page ${current.page_number} of ${pages?.length ?? "…"} · ${routeLabel(current)}` : "—"}
          </span>
        </span>
        <button onClick={onClose} className="hv-114a69" style={{ display: "grid", placeItems: "center", width: "30px", height: "30px", borderRadius: "9px", background: "transparent", border: "1px solid var(--app-line)", color: "var(--app-tx-3)", cursor: "pointer" }}>
          <i className="ph-duotone ph-x" style={{ fontSize: "15px" }}></i>
        </button>
      </div>

      <div style={{ flexShrink: "0", display: "flex", alignItems: "center", gap: "8px", padding: "11px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "4px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "10px", padding: "3px" }}>
          {documents.map((d) => {
            const on = d.id === activeDocumentId;
            return (
              <button
                key={d.id}
                onClick={() => onPickDocument(d.id!)}
                title={d.filename}
                style={{ background: on ? "var(--app-tx)" : "transparent", border: "0", color: on ? "var(--app-bg-2)" : "var(--app-tx-2)", borderRadius: "7px", padding: "5px 10px", fontFamily: "var(--app-font)", fontSize: "11.5px", fontWeight: "600", cursor: "pointer", maxWidth: "170px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", transition: "all 160ms cubic-bezier(0.32,0.72,0,1)" }}
              >
                {d.filename}
              </button>
            );
          })}
        </div>
        <span style={{ flex: "1" }}></span>
        <div style={{ display: "flex", alignItems: "center", gap: "3px", background: "var(--app-bg-2)", border: "1px solid var(--app-line)", borderRadius: "10px", padding: "3px" }}>
          <button onClick={() => setZoomIndex((i) => Math.max(0, i - 1))} className="hv-1a63cc" style={ZOOM_BTN}>
            <i className="ph-duotone ph-minus" style={{ fontSize: "15px" }}></i>
          </button>
          <button onClick={() => setZoomIndex(2)} style={{ background: "transparent", border: "0", color: "var(--app-tx-2)", fontFamily: "var(--app-font)", fontSize: "11.5px", fontWeight: "600", cursor: "pointer", padding: "0 6px", minWidth: "44px" }}>
            {Math.round(zoom * 100)}%
          </button>
          <button onClick={() => setZoomIndex((i) => Math.min(ZOOMS.length - 1, i + 1))} className="hv-1a63cc" style={ZOOM_BTN}>
            <i className="ph-duotone ph-plus" style={{ fontSize: "15px" }}></i>
          </button>
        </div>
      </div>

      <div style={{ flex: "1", minHeight: "0", display: "flex", gap: "10px", padding: "0 16px 16px", overflow: "hidden" }}>
        <div style={{ flex: "1", minWidth: "0", overflow: "auto", background: "var(--app-bg)", border: "1px solid var(--app-line)", borderRadius: "12px" }}>
          {current?.raster_url ? (
            <div style={{ position: "relative", width: `${660 * zoom}px`, transition: "width 200ms cubic-bezier(0.32,0.72,0,1)" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={current.raster_url}
                alt={`Page ${current.page_number}`}
                style={{ display: "block", width: "100%", height: "auto", background: "#fff" }}
              />
              {showOverlay && region ? <Highlight region={region} /> : null}
            </div>
          ) : (
            <div style={{ padding: "40px 24px", textAlign: "center", color: "var(--app-tx-3)", fontSize: "12px", lineHeight: 1.6 }}>
              {current ? "This page has no rendered image yet." : "Nothing to show — upload a bid set first."}
            </div>
          )}
        </div>

        <div style={{ flexShrink: "0", width: "56px", display: "flex", flexDirection: "column", gap: "8px", overflowY: "auto", overflowX: "hidden" }}>
          {(pages ?? []).map((p) => {
            const on = p.page_number === current?.page_number;
            const skipped = p.ocr_route === "SKIP";
            return (
              <button
                key={p.id}
                onClick={() => setPage(p.page_number!)}
                title={`${pageTitle(p)} · ${routeLabel(p)}`}
                className="hv-f68886"
                style={{ width: "52px", height: "66px", flexShrink: "0", background: "var(--app-bg-2)", border: `1px solid ${on ? "var(--app-accent)" : "var(--app-line)"}`, borderRadius: "8px", boxShadow: on ? "var(--app-sh-2)" : "none", cursor: "pointer", display: "flex", flexDirection: "column", justifyContent: "space-between", padding: "7px 6px", fontFamily: "var(--app-font)", opacity: skipped ? 0.5 : 1, transition: "all 160ms cubic-bezier(0.32,0.72,0,1)" }}
              >
                <span style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ height: "2px", borderRadius: "2px", background: skipped ? "var(--app-line)" : "var(--app-accent-line)", width: "80%" }}></span>
                  <span style={{ height: "2px", borderRadius: "2px", background: "var(--app-line)", width: "60%" }}></span>
                  <span style={{ height: "2px", borderRadius: "2px", background: "var(--app-line)", width: "72%" }}></span>
                </span>
                <span style={{ fontSize: "9px", color: "var(--app-tx-3)", textAlign: "right" }}>{p.page_number}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

const ZOOM_BTN: React.CSSProperties = {
  display: "grid",
  placeItems: "center",
  width: "26px",
  height: "26px",
  borderRadius: "7px",
  background: "transparent",
  border: "0",
  color: "var(--app-tx-2)",
  cursor: "pointer",
};

/** The cited elements, as one absolutely-positioned SVG over the page image. */
function Highlight({ region }: { region: SourceRegion }) {
  const points = (poly: [number, number][]) => poly.map(([x, y]) => `${x * 100},${y * 100}`).join(" ");

  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
      }}
    >
      {region.polygons.map((poly, i) => (
        <polygon
          key={i}
          points={points(poly)}
          fill="rgba(129,140,248,0.22)"
          stroke="#818cf8"
          strokeWidth={0.35}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

function pageTitle(p: DocumentManifest): string {
  const cls = (p.page_class ?? "UNKNOWN").replaceAll("_", " ").toLowerCase();
  return cls.charAt(0).toUpperCase() + cls.slice(1);
}

/** Why this page was, or was not, read — the audit answer §4.3 requires. */
function routeLabel(p: DocumentManifest): string {
  switch (p.ocr_route) {
    case "TEXTRACT_TABLES":
      return "read as a table";
    case "TEXTRACT_TEXT":
      return "read as text";
    case "NATIVE_TEXT":
      return "text taken from the file";
    case "SKIP":
      return "not read";
    default:
      return p.ocr_route ?? "—";
  }
}
