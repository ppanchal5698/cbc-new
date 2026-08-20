# ADR-0003 — Classify every page before spending anything on OCR

**Status:** accepted · **Date:** 2026-08-20 · **Spec:** §4, bottleneck B1, Risk R1

## Context

Every source design went from "upload PDF" straight to "call Textract on the
document". For invoices that is right. For architectural bid sets it is not: a set is
40–200+ pages of drawings, of which typically **3–8** carry the door, frame, and
Division 08 hardware schedules.

`AnalyzeDocument` with TABLES costs $0.015 per page. Running it over everything means
paying drawing-sheet prices for drawing sheets that contain no extractable data, and
then feeding the model tens of thousands of irrelevant elements.

## Decision

Classify every page **before** the first OCR call, and route each page individually
through a table loaded from `config/ocr_routes.json`:

| Route | Cost per 1,000 pages |
|---|---|
| `TEXTRACT_TABLES` | $15.00 |
| `TEXTRACT_TEXT` | $1.50 |
| `NATIVE_TEXT` | $0 |
| `SKIP` | $0 |

Five classification tiers, cheapest first: PDF bookmarks, title-block sheet number,
whitespace-tolerant keyword anchors, a Haiku call on a thumbnail, and manual override.
The manifest is persisted **before** any OCR call, because it is the audit answer to
"why was page 47 never read?".

## Why

Measured on the reference bid set (65 pages, Dutch Bros VA0202): **$0.12 triaged
versus $0.98 reading every page**, at 100% recall on the schedule pages. On a 200-page
set the same ratio is roughly $0.13 against $3.00.

Routing is **configuration, not `if` statements** (Risk R1). Open Item 9 — where fire
ratings actually live — is still unanswered by CBC. If the answer turns out to be
"sometimes in drawing margin notes", that is a one-line change to `DRAWING.route` and
a redeploy, not a code change inside a pipeline stage.

Recall is weighted far above precision, deliberately. A false positive costs $0.015. A
false negative costs an opening that never reaches the quote, and that one is found by
the customer.

## Consequences

- A misclassified schedule page is the expensive failure, so the manifest API exposes
  every `SKIP` with its reason and offers a force-read override (Risk R12). Each
  override writes a `feedback` row, which is also how the empirical answer to Open
  Item 9 accumulates.
- The routing table's content hash is folded into the OCR idempotency key: the same
  PDF under a new table is genuinely different work and must not be deduplicated
  against the previous run.
- Classifier recall is a gated metric in `tests/golden/` and fails CI on regression.
