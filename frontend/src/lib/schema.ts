/**
 * Ergonomic aliases over the generated OpenAPI types.
 *
 * `types.generated.ts` is written by `make types` from `backend/schema.yml` and is
 * never hand-edited (§8.2, defect H2). This file exists only so components can
 * write `Project` instead of `components["schemas"]["Project"]`.
 */

import type { components } from "./types.generated";

export type Schemas = components["schemas"];

export type Project = Schemas["Project"];
export type Document = Schemas["Document"];
export type DocumentManifest = Schemas["DocumentManifest"];
export type PipelineJob = Schemas["PipelineJob"];
export type Opening = Schemas["Opening"];
export type FieldProvenanceGrid = Schemas["FieldProvenanceGrid"];
export type Match = Schemas["Match"];
export type Quote = Schemas["Quote"];
export type QuoteLine = Schemas["QuoteLine"];
export type VendorRFQ = Schemas["VendorRFQ"];
export type HardwareSetComponent = Schemas["HardwareSetComponent"];

// Reference library and price books (§7.5). Every one of these carries an
// effective date, because NFR-3 requires a quote to be traceable to the exact
// sheet version and tier that priced it — not merely to "the multiplier".
export type CatalogItem = Schemas["CatalogItem"];
export type MarginBand = Schemas["MarginBand"];
export type VendorMultiplier = Schemas["VendorMultiplier"];
export type TaxRate = Schemas["TaxRate"];
export type FinishCode = Schemas["FinishCode"];
export type ThroatDepth = Schemas["ThroatDepth"];
