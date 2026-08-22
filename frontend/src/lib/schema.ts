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
export type CatalogItem = Schemas["CatalogItem"];
export type Quote = Schemas["Quote"];
export type QuoteLine = Schemas["QuoteLine"];
export type VendorMultiplier = Schemas["VendorMultiplier"];
