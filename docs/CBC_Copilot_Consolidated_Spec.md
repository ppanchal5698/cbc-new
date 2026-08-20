# CBC Estimating & Pricing Copilot — Consolidated Engineering Specification

**Version:** 3.0 · **Date:** 19 August 2026 · **Owner:** Dash Technologies (delivery partner)
**Client:** Construction Building Components (CBC), a division of The Hamilton Parker Company, Columbus OH

**This document supersedes and replaces:**

| Superseded document | Version / date | Disposition |
|---|---|---|
| `requirements.md` — Requirements Validation Workbook | v1.3, 14 Jul 2026 | Compressed into §2. Retain the original as the signed client artefact; this is the engineering restatement. |
| `CBC_Construction_Estimation_Domain_Guide.md` | — | Compressed into §1. |
| `CBC_Estimator_Pipeline_Plan.md` — Technical Build Plan | — | Absorbed into §3, §5, §6, §7. |
| `CBC_Development_Plan.md` | approved 18 Aug 2026 | Absorbed into §7, §12, §13, §14. |
| `Estimating_Copilot_Architecture.md` | v2, 18 Aug 2026 | Absorbed into §3, §10, §11. **Corrected** — see §0.2. |

**New in v3.0:** §4 Document preprocessing · §5 Extraction best practices · §8 Project structure · §9 Bottlenecks and resolutions · §10 Cost model and AWS billing reduction playbook.

---

## Table of contents

| § | Section |
|---|---|
| 0 | Document control — conflicts found and resolved |
| 1 | Domain context — what CBC estimates and how |
| 2 | Requirements — FR / NFR / open items |
| 3 | Target architecture |
| 4 | Document preprocessing |
| 5 | Extraction pipeline and best practices |
| 6 | Matching and pricing engines |
| 7 | Data model |
| 8 | Project structure — backend, frontend, infra, environments |
| 9 | Bottlenecks — identified and resolved |
| 10 | Cost model and AWS billing reduction playbook |
| 11 | Security, compliance, observability |
| 12 | Delivery plan and verification |
| 13 | Risk register |
| 14 | Open items and decisions required |
| A | Appendix — decision log, environment variables, glossary |

---

# 0. Document control — conflicts found and resolved

The five source documents were written at different times against different assumptions (an Azure stack, then AWS; MySQL, then Postgres; a single-service deployment, then a hybrid one). Merging them without reconciliation would have carried forward contradictions that produce wrong builds. Every conflict found is listed below with its resolution. **Where a resolution needs CBC or Dash sign-off rather than an engineering call, it is marked ⚠ and repeated in §14.**

## 0.1 Method

Each source was read in full and cross-checked field by field on: technology selections, sizing figures, cost arithmetic, schema shapes, requirement identifiers, and stated repository state. Seventeen conflicts or defects were found. Nine are resolved here on engineering grounds; eight need a decision.

## 0.2 Conflicts and defects — resolution table

| # | Conflict | Sources | Resolution |
|---|---|---|---|
| **C1** | **Repository state contradicts itself.** The Development Plan header states *"No implementation code has been written yet"* and §Context states the repo *"implements exactly one stage — intake."* But §1.1 marks the **Textract pass** (`backend/app/textract.py`, `worker.py`) and the **Normalization layer** (`backend/app/normalize.py`) as **WORKING**. Both cannot be true. | Dev Plan L3, L13 vs L34–35 | **Resolved as:** intake is *verified* working (14 passing tests). `textract.py` / `worker.py` / `normalize.py` exist but are **unreachable** — defect H1 means the worker container cannot boot, so this code has never executed end to end. Status is **written, never executed**, not *working*. Phase 0 must re-audit it against §4 and §5 before it is trusted. Treat any figure derived from "it already works" as unproven. |
| **C2** | **Database engine.** Architecture v2 lists *Amazon RDS for PostgreSQL 17* as currently provisioned by Terraform. The Development Plan says the live application is **MySQL 8.4**, with `RDS_ENGINE_VERSION=8.4` and `ensure_rds_instance.py` both hardcoding MySQL, and lists the Postgres move as unstarted Phase 0 work. | Arch §1, §2 vs Dev Plan §Confirmed decisions, Q4 | **Resolved as:** Postgres 17 is the **target**; MySQL 8.4 is the **current** state. Terraform declaring Postgres does not make the application use it. §8 and §12 treat the migration as Phase 0, blocking. ⚠ Q4 (drop the dev dataset vs. migrate in place) blocks Phase 0 and must be answered first. |
| **C3** | **Authentication.** Architecture v2 selects **Amazon Cognito** as the auth layer. The Development Plan describes a working Django auth app (`authentication_user`, `auth/tests.py`) and a Next.js auth shell, and never mentions Cognito. | Arch §2 vs Dev Plan §1.1, §4.3 | **Resolved as:** **Django auth is the system of record** through Phase 5. Cognito is removed from the near-term stack. It solves a problem this build does not have (10 known internal users, already authenticated), and adding it now imports a token-exchange integration into the highest-risk phase. If SSO is later required, Cognito or Entra ID sits *in front of* Django as an OIDC provider — Django remains the authorisation and audit boundary. Removes ~1 integration and simplifies §10 sizing. |
| **C4** | **Compute topology and sizing.** Architecture v2 sizes **one t3.large** for *"the FastAPI application + background worker"* and its stack table has **no Django and no frontend at all**. The Development Plan's real topology is **four services**: Django/DRF, FastAPI worker, Next.js, Postgres. | Arch §2, §3 vs Dev Plan §Service topology | **Resolved as:** the architecture doc sized a system that does not exist. Corrected topology and sizing in §3.4; corrected cost model in §10. Headline change: the worker is moved **off** the API host (§9 B1/B5 explain why), and the frontend is given an explicit home. |
| **C5** | **Bedrock model identifier.** Architecture v2 hardcodes `anthropic.claude-opus-5`. This is not a resolvable Bedrock model ID or inference-profile ID. | Arch §2 | **Resolved as:** **never hardcode a model ID in a document or in code.** Resolve at deploy time via `bedrock:ListFoundationModels` / `ListInferenceProfiles`, pin the resolved identifier in SSM Parameter Store, and record the resolved value on every `extraction_runs` row so a re-run is attributable to an exact model version. ⚠ Q1 (region + model access grant) still applies. |
| **C6** | **SQS FIFO recommended for "retries."** Architecture v2 §6 lists *"Need guaranteed message ordering / retries → migrate to SQS FIFO."* Retries are not a FIFO feature; standard SQS already retries via visibility timeout plus a redrive policy. | Arch §6 | **Resolved as:** FIFO is warranted only if strict ordering or exactly-once dedup is required — neither applies here. What is **actually missing from both documents** is a **dead-letter queue and redrive policy**. Added in §3.2 and §9 B7. |
| **C7** | **Quote PDF renderer.** Pipeline Plan §3.9 and Dev Plan §1.1 both say **ReportLab**. The Architecture stack table lists **no renderer at all** while listing generated quote PDFs as an S3 output. | Pipeline §3.9, Dev Plan §1.1 vs Arch §2 | **Resolved as:** renderer added to the stack. **Recommendation: HTML + CSS → WeasyPrint**, because FR-10 requires matching CBC's *existing* customer-facing layout — a table-heavy document exported from Excel — and CSS table layout reproduces that faster and more maintainably than ReportLab's imperative canvas. ReportLab is the fallback if the layout needs vector drawing primitives. ⚠ Not finally decidable until Q10 (the actual layout file) is provided. |
| **C8** | **Provenance citation shape.** Pipeline Plan §3.5 specifies `source_element_ids UUID[]` on `field_provenance`. Dev Plan §3.2 replaces it with a join table and `ON DELETE RESTRICT`. | Pipeline §3.5 vs Dev Plan §3.2 | **Resolved as:** the **join table wins**, and the Dev Plan's reasoning is adopted verbatim: an array column carries no referential integrity, and the entire traceability contract is *"if the model cannot point to a real element, the field is rejected."* That contract belongs in the database, not in application code that can be bypassed. §7 states one shape only. |
| **C9** | **`di_` table prefix.** Three separate documents carry a footnote apologising that `di_elements` / `di_path` are *"historical identifiers, not an Azure product."* They are in fact vestigial Azure Document Intelligence names. | All three technical docs | **Resolved as:** **rename now.** `di_elements` → `doc_elements`, `di_path` → `element_path`, `di_confidence` → `ocr_confidence`, `di_result_key` → `ocr_result_key`. The migrations are being squashed and no production data exists, so the rename costs nothing today and removes a permanent source of confusion for every future engineer. |
| **C10** | **`backend/apps/` vs `backend/app/`.** Django apps live in `backend/apps/`; the FastAPI worker lives in `backend/app/`. Two different services, one character apart. | Dev Plan §1.1 | **Resolved as:** renamed to `backend/api/` (Django) and `backend/pipeline/` (FastAPI) in §8. |
| **C11** | **Freight.** FR-7 requires a freight line on the draft quote. Open Item 1 answers that freight is *generally not quoted at estimate stage*. | Req FR-7 vs Open Item 1 | **Resolved as** (per Dev Plan, restated once here): freight is a **line item with a nullable amount**, never a computed number. It renders as `TBD` unless an estimator enters a value. Both requirements are satisfied. |
| **C12** | **Cost model omits real line items.** The Architecture cost table has no EBS volume, no RDS storage or backup, no data transfer, no NAT gateway or VPC endpoints, no frontend hosting, no S3 request charges, no DNS. | Arch §5 | **Resolved as:** complete cost table in §10.2, plus the optimisation playbook in §10.3. Corrected naive baseline is **~$212/month**, not $187. |
| **C13** | **FR-15 contradicts Open Item 14.** FR-15 and NFR-8 require below-floor margin flagging *and approval routing*. Open Item 14 answers *"no margin deviation today; approval routing deferred"* and marks FR-15 **Out of scope (future)**. | Req FR-15, NFR-8 vs Open Item 14 | **Resolved as** (per Dev Plan R4): build the **flag** (`below_floor_flag`, configurable floors); build **no** approval workflow. ⚠ Surface the tension to CBC rather than resolving it silently — it is a live requirement and a deferred one pointing at the same feature. |
| **C14** | **Confidence threshold 0.80 presented as a design constant.** Pipeline Plan §3.4 gives 0.80 as the review threshold; the Dev Plan (R7) correctly notes it is a suggestion, not a CBC-validated number. | Pipeline §3.4 | **Resolved as:** 0.80 is a **placeholder default in configuration**, never a constant in code. §5.9 specifies the calibration procedure that replaces it with a measured value. |
| **C15** | **Frontend framework version.** The Dev Plan repo audit says **Next.js 16**. The Architecture doc omits the frontend entirely. Earlier planning material referenced Next.js 15. | Dev Plan §1.1 | **Resolved as:** Next.js 16 (App Router), per the most recent repository audit. ⚠ Confirm at Phase 0 against `package.json` — a major-version discrepancy in the record is itself a signal the audit needs refreshing. |
| **C16** | **Textract's 3,000-page / 500 MB async limit is stated but never handled.** Architecture and Pipeline Plan both quote the limit; neither says what happens when a full architectural plan set exceeds it. Full plan sets routinely do. | Arch §5, Pipeline §3.2 | **Resolved as:** §4.6 adds a mandatory pre-Textract splitting strategy with stable page-offset bookkeeping so provenance survives the split. |
| **C17** | **NFR-6 ("reviewable draft in minutes") is unreachable as designed.** The Architecture doc's own justification for a 15-minute SQS visibility timeout is that *"Textract on a 65-page architectural set takes minutes."* If OCR alone consumes the budget, the end-to-end target cannot be met once extraction, matching, and pricing are added. | Arch §3 vs NFR-6 | **Resolved as:** §4 page triage is what makes NFR-6 achievable — a typical bid set has 3–8 schedule pages inside a 40–200 page plan set. OCR-ing 5 pages instead of 200 changes the OCR budget from minutes to seconds and is simultaneously the largest cost lever (§10.3). This is the single most important addition in v3.0. ⚠ NFR-6 still has no formal numeric target (Open Item 16). |

## 0.3 Defects carried forward from the repository audit

These are unchanged from the Development Plan and remain open work in Phase 0.

| # | Defect | Location |
|---|---|---|
| H1 | `worker` container cannot start — its command invokes `ensure_sqs_queue` and `run_pipeline_worker`, neither of which exists. With `restart: unless-stopped` it crash-loops on every `docker compose up`. **Root cause of C1.** | `docker-compose.yml:106` |
| H2 | Frontend reads fields the API stopped returning after migration 0008 — silently `undefined` at runtime. | `frontend/src/lib/types.ts`, `project-files-table.tsx:147`, `projects/[id]/page.tsx:206` |
| H3 | README references files that do not exist: `make logs-docker`, `scripts/docker_logs.py`, `hercules/run.ps1`, `mysql-server`/`mysql-ui` compose services. | `backend/README.md` |
| H4 | `modelsApi` is a stub (`// TODO: replace when GET /api/models/ exists`) backing a live `/models` page. | `frontend/src/lib/api.ts:156` |
| H5 | Dead shims `project_upload_to`, `default_requested_stages`, `empty_dict`, `empty_list` survive only because migrations 0003–0007 import them by path. Removed by the squash. | `backend/apps/projects/models.py:141-154` |

---

# 1. Domain context — what CBC estimates and how

This section is the compressed domain guide. Everything here is a confirmed business rule that the pipeline must reproduce exactly; none of it is engineering preference.

## 1.1 The business

CBC is the national-accounts division of The Hamilton Parker Company. It quotes and supplies **commercial building components — never installed labour** — to general contractors, franchisees, and architects, concentrated in retail and quick-serve restaurant chains (McDonald's, Wendy's, Cava).

| Product family | CSI Division | Covers |
|---|---|---|
| Doors & Frames | 08 — Openings | Hollow-metal and wood doors; HM frames, welded or knock-down |
| Door Hardware | 08 — Openings | Hinges, locks, exit devices, closers, full hardware sets |
| Division 10 Specialties | 10 — Specialties | Toilet partitions, restroom accessories, washroom equipment, hand dryers |
| FRP wall panels | 09 — Finishes (adjacent) | Fibreglass-reinforced plastic panels; quantities via Vu360 takeoff |

**Out of scope:** aluminium/glass storefront, coiling and overhead doors, oversized doors, garage doors (separate HP division), ceiling tile and grid, tile, brick, masonry, engineered wood, extruded aluminium siding, Scranton (access lost).

**Commercial basis:** supply-only material. Hamilton Parker PO required; 30-day validity. Sales tax applies **only in Ohio (~8%) and Kentucky (6.5%, border nexus)**; the other 48 states and Canada are untaxed because the sale is to a GC or corporation, not the end customer. CBC always sells to the GC's internal initiator — never directly to the architect.

> **Engineering note.** Tax rates are **reference data with effective dates**, not constants. Ohio's rate is county-dependent and the quoted ~8% is a working figure. A hardcoded 0.08 becomes a wrong invoice the first time a rate changes.

## 1.2 A bid set

The package an estimator receives, arriving as **one combined PDF or several separate PDFs**:

- **Drawings / plans** — architectural sheets: layout, wall types, opening locations.
- **Door schedule** — a table listing every opening by number with size, type, and attributes.
- **Hardware schedule / Division 08 spec section** — named hardware sets (`HW-1`, `HW-2`), increasingly with explicit manufacturer part numbers instead of abstract grades.
- **RFP / quote-request text** — usually email body alongside the plans; sometimes phoned in.

Reading specs and drawings is confirmed to be **the single largest time cost in producing any estimate**. That is the step the copilot compresses.

## 1.3 Anatomy of an opening — the extraction targets (FR-2)

| Field | Rule |
|---|---|
| **Door number** | Unique opening identifier (`101`, `D-12`). Everything else keys to it. |
| **Size** | Fixed 4-digit shorthand: first two digits width, last two height, feet-inches. `3070` = 3'-0" × 7'-0"; `3670` = 3'-6" × 7'-0". A **fixed parsing rule**, never inferred per document. |
| **Handing** | `LH` / `RH` / `LHR` / `RHR`. Determines which handed lock, closer, or exit device is ordered — handed parts are separate SKUs. Stated per opening in the schedule. |
| **Fire rating** | **20 / 45 / 60 / 90 minutes** (UL 10C / NFPA 252). Drives door core, frame, and hardware line — rated hardware is a distinct certified product line, not a spec note. A dropped or wrong rating is a **code-compliance failure, not a cosmetic error**. ⚠ Where the rating lives on a CBC bid set is still **open** (Open Item 9). |
| **Finish** | Two naming systems in simultaneous use; both must be interpreted. |
| **Hardware group** | Named set (`HW-3`) defined in the Div 08 spec, **or** an explicit manufacturer part/series (e.g. Hager 3400 vs 3500 = ANSI/BHMA Grade 1 vs Grade 2). CBC quotes the exact part/series, then reconciles against its stocked top-10 (extendable to ~20) library, with a custom/other tab beyond. |
| **Alternate designation** | Base bid vs `Alternate 1`, `Alternate 2`. A **first-class attribute** — base and alternate totals must present as separate comparable figures. ⚠ Reconciliation process still **open** (Open Item 11 / FR-14). |

**Finish code normalisation (NR-3) — seed data:**

| Legacy US code | BHMA code | Description |
|---|---|---|
| US26D | 626 | Satin chrome on brass — most common interior commercial |
| US26 | 625 | Bright polished chrome |
| US32D | 630 | Satin stainless — most common exit device / hinge / exterior |
| US32 | 629 | Bright stainless |
| US19 | 622 | Flat black |
| US15 | 619 | Satin nickel |

> **US19 and US26D must never collapse to the same row.** Estimators flagged this explicitly. They are different satin finishes on different base metals, mapping to different BHMA codes. A matcher that treats "satin" as a fuzzy token will conflate them.

**Frame throat depth by wall type** — five standard sizes cover the large majority; anything else routes to a manually entered custom value (cap ~10 total). A **table**, not a hardcoded pick-list.

| Throat depth | Wall type |
|---|---|
| 5-5/8" | Half-inch drywall (common at McDonald's-type builds) |
| 5-3/4" | Masonry |
| 5-7/8" | Drywall (alternate spec) |
| 7-3/4" | Wood-frame variant |
| 8-1/4" | 6" metal stud with 5/8" drywall |

**Hardware set anatomy:** continuous or butt hinges, lock or exit device, closer, kick plate, threshold, door sweep, weatherstrip, smoke seal, floor stop/holder, silencers. **There is no single universal CBC standard list** — sets are built around top stock items per product type and vendor series, not abstract hardware grade.

**Keying** is handled inside the lock's own option set (interchangeable core small/large format, storeroom lock with IC, architect-specified keyway) — **not** as a separate keying-schedule workflow.

## 1.4 Vendors and sourcing

Roughly 90% of quote volume: **Hager (~75% of hardware)**, **Allegion** (Von Duprin, LCN, Schlage, Ives), **Pemko**, **National Guard**, **Rockwood**, **Cal-Royal**, **Alarm Lock** (hardware); **Bobrick**, **Bradley**, **ASI** (partitions and accessories); **World Dryer**, **Excel XLERATOR** (hand dryers); **Five Lakes**, **Pioneer**, **Masonite Architectural**, **Special-Lite**, **HP Fabrication** (doors); **Marlite**, **NUDO** (FRP).

Some lines are bought **direct**; others go through a **distributor** — Allegion via Banner Solutions or SecLock, laminate via Pionite or Wilsonart — requiring a **manual price check** by phone or website. Manual price entry is a *required* path, not a fallback (NR-2).

**Direct-equal substitution:** when a drawing specifies only a hardware *function* with no manufacturer, CBC asks the GC to approve a "direct equal" so a preferred line (usually Hager) can be quoted. When a specified line is unavailable, the estimator proposes the closest of the top 2–3 competing brands with a note. **This is estimator judgment, not a rule the system decides** — the system records the substitution and the note; it does not choose the substitute.

## 1.5 Pricing rules

**Cost sourcing — three paths, strict priority order:**

1. **Last-PO price from P21** (CBC's Prophet 21 ERP) — used when the item sold within the last 12 months with no intervening price increase. Primary path for ~90% of items. Preferred **over** P21's own supplier-cost fields, which purchasing does not keep current.
2. **Distributor price sheet** — Banner/SecLock (Allegion), Pionite/Wilsonart (laminate) and similar, when not bought direct.
3. **Manufacturer list price** — for items never sold direct before.

Cost data older than **6–8 months is stale**, must be discarded and refreshed. ⚠ The exact window is configurable; no named data steward exists (Open Item 15).

**List × multiplier:** for non-special-priced items, cost = manufacturer list × a customer-specific discount multiplier tied to CBC's negotiated tier (e.g. Hager's tier yields ~71% off list, a 0.29 multiplier). **MAP is not cost** — MAP governs advertising, not what CBC pays. Manual **adders** (electrification, non-removable-pin hinges, premium/lead-time finishes) sit outside the base price book and are added on top (NR-4).

**Vendor RFQ:** for large, custom, non-stock, or first-time items — a 9-foot door, an unusual prep, an option not sold in years — CBC requests a live quote. Slower path: request out, wait, enter by hand. There is a **hard cut-off beyond which an item cannot be priced automatically and stays manual** (NR-13).

**Margin — applied as a divisor, not a markup.** Stable for 14 years, overridable per line.

| Product-type band | Margin | Divisor |
|---|---|---|
| Commodity | 27% | 0.73 |
| Restroom partitions | 35% (accessories derive ~56% from real data) | 0.65 |
| Specialty (e.g. laminated doors) | 40% | 0.60 |
| Custom-built via outside fabricator | 25% | 0.75 |

**The quote calculation.** Only **three** fields per line are human-entered: **Quantity**, **Our Cost**, **Margin**. Everything else derives:

```
sale_each  = our_cost / (1 - margin_pct)
extended   = sale_each * quantity
subtotal   = SUM(extended) per group
grand_total= SUM(subtotal)
```

The legacy **`unit_weight`** field (originally for truck-loading) is confirmed obsolete and **is not rebuilt**.

## 1.6 The estimator's current workflow

| Phase | Activity | Tool today |
|---|---|---|
| 0 — Intake | Bid arrives, usually by email with job workbook + plans/RFP; sometimes by phone. From the internal initiator in the queue (Kellan, Matt, Rebecca, Tina) — never the architect. | Outlook |
| 1 — File setup | Save-As from a prior job's workbook; clear residual rows. | Excel (password `ESTIMATOR`) |
| 2 — Spec scoping | Read specs to identify Div 08 and Div 10 scope, fire ratings, HW schedule. | PDF viewer |
| 3 — Drawing review & takeoff | Count and measure quantities off drawings. | Edge viewer / Vu360 |
| 3b — FRP takeoff | Set scale in Vu360, capture perimeter LF and corners; convert to material quantities **by hand**. | Vu360 + calculator |
| 4 — Pricing | Build the quote line by line using the cost and margin logic above. | Excel + P21 + vendor sheets |
| 5 — Judgment, reuse, RFIs | Reuse similar past jobs; handle direct-equal substitutions; raise RFIs. | Excel / email |
| 6 — Deliver | Export PDF proposal, send **back to the specific initiator**, not a group inbox. | Excel → PDF → Outlook |

Two modes run in parallel: **templated** (open a brand master, trim down — Shanna) and **one-off** (build from blank — Kevin; exceptions McDonald's, Cava). Rick works from his own Excel. Both modes stay.

**Knowledge-continuity risk is a confirmed priority.** Estimating knowledge is concentrated in **Kevin, Rick, and Shanna**. Capturing rules and reference data is part of the mandate, not a side effect.

## 1.7 Why the domain shapes the engineering

- Fire rating and handing must be extracted with **zero tolerance for silent drops** — an unrated door in a rated opening or a wrong-handed lock is a defect, not a preference. This is why §5.8 gives them a separate, stricter validation path.
- Finish-code ambiguity is an **industry-wide parsing trap**, not a CBC quirk. Normalisation must handle both systems (§7 `finish_codes`).
- Margin-as-divisor and the three-path cost waterfall mirror 14 years of negotiated vendor relationships. The pricing engine **replicates them exactly** — no LLM, no simplification (§6.2).
- CBC has not answered where fire rating lives or how alternates reconcile. The system must therefore **flag these for human confirmation** rather than assume a fixed document location (§4.3, §5.8).

## 1.8 Glossary

| Term | Meaning |
|---|---|
| GC | General contractor — who CBC actually sells to |
| RFP / RFQ | Request for proposal (ask for a quote) / request for quote (vendor-facing cost inquiry) |
| Takeoff | Measuring or counting quantities directly off drawings |
| P21 (Prophet 21) | CBC's ERP — source of purchase history and cost |
| HM / FRP | Hollow metal / fibreglass-reinforced plastic |
| Handing (LH/RH/LHR/RHR) | Swing and hinge side relative to the viewing side |
| BHMA | Builders Hardware Manufacturers Association — the modern numeric finish standard |
| MAP | Minimum advertised price — a marketing floor, **not** CBC's cost |
| Bid alternate | Optional scope variant priced separately from the base bid |
| Addendum | A formal, dated revision to bid documents issued before award |
| Direct equal | Estimator-proposed substitute when a spec names only a function or an unavailable brand |
| Keying | How a lock's key system is configured (alike, different, master, construction, keyless) |
| Vu360 | Specialist digital takeoff tool used for FRP panel geometry |

---

# 2. Requirements

Compressed from the v1.3 Requirements Validation Workbook. Of 54 requirement rows, ~50 carry a confirmation from the 14 Jul estimator validation session (Kevin, Rick, Shanna). The unresolved items are consolidated in §14.

**Guiding principle, quoted from the workbook and binding on every design decision below:**

> The estimator stays in control of every quote. The copilot drafts, sources, and calculates — it does not send. Its job is to remove manual re-keying and lookup, not to replace estimating judgment.

## 2.1 Functional requirements

| Ref | Requirement | Priority | Status | Engineering note |
|---|---|---|---|---|
| **FR-1** | Accept a bid-set PDF (and associated email / RFP text) as the trigger for a new estimate. | Must | Confirmed | Upload path works. Add `source_channel`, `initiator_email`, `rfp_body_text`. Manual "create bid request" for phone-in (NR-5). Email/Graph webhook is **optional for Phase 1** — manual upload already satisfies the trigger. |
| **FR-2** ⚠ | Extract door/opening schedule data — number, size, handing, finish, **fire rating**, hardware-group callouts, alternate designation. | Must | Confirmed | The core of §4 and §5. Fire-rating location still open. |
| **FR-3** | Maintain a central structured reference library of hardware sets and standard line items, **independent of any single job file**. | Must | Confirmed | `catalog_items` + reference tables (§7.5). Explicitly *not* per-project — this is the fix for the Excel-workbook-per-job status quo. Blocked on NR-6. |
| **FR-4** | Match each extracted opening to the closest library entry — respecting rating, handing, finish — and propose line items. | Must | Confirmed | Rating and handing are **hard** constraints; finish is scored. Manual cut-off beyond heavy customisation (NR-13). §6.1. |
| **FR-5** | Apply the product-type margin framework as an **editable default** per line. | Must | Confirmed | `margin_bands` as data with effective dates, not constants. Overridable per line with a logged reason. |
| **FR-6** | Source cost from P21 last-PO (**not** supplier-list fields), or vendor list × customer multiplier, or a vendor-RFQ price — honouring freshness and recording the source. | Must | Confirmed | Cost waterfall with `MANUAL` as a **first-class path from day one** (Risk R3). |
| **FR-7** | Generate a draft quote grouped by door with subtotals, a separate restroom-accessories block, and a freight line. | Must | Confirmed | Freight is a **line, not a computed number** (C11). |
| **FR-8** | Confidence score per match; flag low-confidence matches, missing ratings, and unparsed content. | Must | Confirmed | Composite score, §5.9. Mirrors the estimator's own P21 behaviour: *"here are 3 close matches — is it one of these?"* |
| **FR-9** | Review/edit interface to accept, edit, delete, or add lines — **nothing sent without explicit approval**. | Must | Confirmed | Plus substitution notes, manual price entry, custom/other tab. |
| **FR-10** | Export the approved quote to PDF in the current customer-facing format with standard commercial terms. | Must | Confirmed | Routes **back to whoever initiated the request** (Kellan/Matt/Rebecca/Tina), **not a group email**. ⚠ Blocked on Q10 (the layout file). |
| **FR-11** | Reuse the closest prior quote (same brand / architect / GC) as a starting draft. | Should | Confirmed | Needs `brand`, `architect`, `general_contractor` on the project record. Phase 5 — depends on having prior quotes in the system. |
| **FR-12** | Assist FRP takeoff: perimeter and corners → quantities using the estimator's constants. | Should | **Deferred** | ⚠ Open Item 5 constants (panel size, waste %, trim/stick lengths, adhesive coverage) outstanding. **Do not build the converter on guessed constants** — plausible wrong quantities are worse than no feature. |
| **FR-13** | Capture estimator corrections as structured feedback to improve future matching. | Should | Confirmed | `feedback` table written on every review-UI edit. This is the tuning dataset (§5.10). |
| **FR-14** ⚠ | Version an estimate — base bid plus alternates; absorb addendum revisions without losing history. | Should | **Pending** | Build the **data model**; gate the UI behind a flag; build **no reconciliation logic** until Open Item 11 is answered. |
| **FR-15** ⚠ | Flag lines below the product-type margin floor and route for approval. | Should | **Conflicted** | See C13. Build the flag; build no workflow. |
| **FR-16** | Vendor-RFQ loop — mark a line "awaiting vendor quote", capture the returned price, slot into the draft. | Could | Confirmed | Manual price entry **required** for distributor lines, with a "price may be out of date — refresh" prompt (NR-2). |

## 2.2 Non-functional requirements and guardrails

| Ref | Guardrail | Status | Engineering realisation |
|---|---|---|---|
| **NFR-1** | Human in the loop — no quote sent without explicit estimator approval. | Confirmed | Hard gate in the state machine; no send path exists without an `APPROVED` transition. |
| **NFR-2** | Accuracy / trust — confidence scoring and review flags visible from day one; unmatched or low-confidence items **never silently guessed**. | Confirmed | The governing design constraint of §5. `*_absent` booleans, citation rejection, value grounding. |
| **NFR-3** | Auditability — every generated line traceable to a source drawing page **and** to a reference-library / price-sheet version, including vendor multiplier tier and effective date. | Confirmed | §7 provenance chain + `vendor_multipliers.source_sheet_version`. |
| **NFR-4** ⚠ | Data security — drawings, pricing, and customer data remain in an approved, access-controlled environment. | **Open** | Cross-cloud egress is **closed**: OCR (Textract) and reasoning (Bedrock) run in the same AWS account as S3 and RDS. Remaining item is CBC IT **naming AWS as the approved environment**. §11. |
| **NFR-5** | Integration — read-only where P21 is involved; no write-back initially. | Confirmed | ⚠ *How* the read happens is still open (Q11). |
| **NFR-6** | Performance — a typical 10–40 opening bid set produces a reviewable draft **in minutes, not hours**. | Aligned, no number | See C17. Achieved via §4 triage. ⚠ Formal target still to be set. |
| **NFR-7** | Usability — usable by senior and junior estimators without specialist training. | Confirmed | |
| **NFR-8** ⚠ | Margin governance — enforce a floor / approval threshold per product type. | Out of scope (future) | See C13. |
| **NFR-9** | Approval authority and QA — who may approve/send, dollar thresholds, QA checkpoint. | Out of scope (future) | |
| **NFR-10** ⚠ | Data stewardship — named owner and refresh cadence per pricing source. | **Open** | Mitigated but not solved by `effective_date` on every price source and a configurable staleness window. Risk R5. |
| **NFR-11** | Adoption and change management — training and rollout across all estimators. | Confirmed | Long-term engagement with ongoing maintenance. |

## 2.3 New requirements and data still outstanding (14 Jul session)

| # | Type | Item | Owner |
|---|---|---|---|
| NR-1 | New req | Light-kit (lites/louvers) pricing calculator — glazing type + size → price from vendor tables (National Guard, PEMKO/Markar, Rockwood). | Build |
| NR-2 | New req | Manual price entry for distributor lines with a "price may be out of date — refresh" prompt. | Build |
| NR-3 | New req | Dual finish-nomenclature interpreter as reference data. | Build |
| NR-4 | New req | Manual adders outside the base price book. | Build |
| NR-5 | New req | "Create new bid request" for phone-in bids. | Build |
| **NR-6** | **Data** | **Top-10 stock list per product type — foundation for the item picker and custom/other tab.** | **CBC — blocks Phase 3** |
| NR-7 | Data | Hager adder values (electrification / NRP / premium finish). | CBC |
| NR-8 | Data | Light-kit table logic (glazing types + size multipliers). | CBC |
| NR-9 | Data | Special-customer margins (e.g. Wendy's). | CBC |
| NR-10 | Investigate | P21 integration feasibility + part-number / "semi-item" matching strategy. | Dash / IT |
| NR-11 | Confirm | Exact term and scope of HP-Fabrication "peelle/peeling" doors. | CBC |
| NR-12 | Investigate | Hager live-data / API feed instead of the static PDF price book. | Dash |
| NR-13 | Principle | **Automate stock / top-N items; beyond that, a clear MANUAL cut-off and custom path. Do not attempt to price every option permutation — the estimator handles the long tail.** | Dash / CBC |

---

# 3. Target architecture

## 3.1 Corrected stack

Changes from Architecture v2 are marked. Everything unmarked is carried forward unchanged.

| Layer | Selection | Purpose |
|---|---|---|
| API / system of record | **Django 5 + DRF**, Gunicorn + Uvicorn workers | Auth, projects, documents, openings, quote endpoints, review, approval, export. **Owns all migrations.** *(added — C4)* |
| Pipeline worker | **Python 3.11 + FastAPI**, SQS consumer as a lifespan task | Preprocess → Textract → normalise → extract → link → match → price |
| Frontend | **Next.js 16 (App Router)**, TypeScript, deployed as a container | Review UI, openings grid, source-highlight viewer *(added — C4/C15)* |
| Compute — API + web | **EC2 t4g.large** (2 vCPU / 8 GiB, arm64, Ubuntu 24.04 LTS) | Django + Next.js *(changed — Graviton, §10.3)* |
| Compute — worker | **EC2 t4g.medium** (2 vCPU / 4 GiB, arm64), separate instance | Pipeline worker isolated from request traffic *(changed — §9 B1)* |
| Document OCR | **Amazon Textract** — async `StartDocumentAnalysis`, `TABLES` + `LAYOUT`, **selectively per page** | Structure extraction from schedule pages *(changed — §4 triage)* |
| Cheap OCR path | **Amazon Textract `DetectDocumentText`** | Spec/prose pages without tables — 10× cheaper *(added — §4.4)* |
| Native text path | **PyMuPDF** text-layer extraction | Vector PDFs with a real text layer — free, no API call *(added — §4.2)* |
| LLM reasoning | **Claude on Amazon Bedrock**, model ID resolved at deploy time and pinned in SSM | Semantic interpretation, matching support, confidence *(changed — C5)* |
| Cheap LLM tier | **Claude Haiku on Bedrock** | Page classification and table location only *(added — §10.3)* |
| Object storage | **Amazon S3** — source bucket (versioning + Object Lock **Governance** mode); derived bucket (versioned, lifecycle-managed, no lock) | Source PDFs, OCR JSON, page rasters, quote PDFs *(changed — §11.3)* |
| Relational DB | **Amazon RDS for PostgreSQL 17**, `db.t4g.medium` | Reference library, provenance, quotes, audit |
| Connection pooling | **PgBouncer** (sidecar) or **RDS Proxy** | Two services × multiple workers against one small instance *(added — §9 B10)* |
| Async queue | **Amazon SQS** standard, 15-min visibility timeout, **+ DLQ with `maxReceiveCount: 3`** | Django → worker handoff *(changed — C6)* |
| OCR completion | **Amazon SNS → SQS** via Textract `NotificationChannel` | Replaces the polling loop *(added — §9 B2)* |
| Authentication | **Django auth** (Cognito deferred) | *(changed — C3)* |
| Secrets | **AWS SSM Parameter Store** (SecureString) | DB URL, Bedrock model ID, API keys |
| Quote rendering | **WeasyPrint** (HTML + CSS → PDF) | Customer-facing proposal *(added — C7)* |
| PDF rasterisation | **PyMuPDF** — pre-rendered page images to S3, **not** per-request cropping | Source-highlight viewer *(changed — §9 B5)* |
| CDN | **CloudFront** in front of the derived bucket | Serves page rasters; keeps rendering off the app host *(added)* |
| Monitoring | **CloudWatch** logs (retention set), metrics, alarms; **X-Ray** on the pipeline | Latency, error, and cost-per-bid tracing |
| Networking | VPC, public subnet for app hosts with no inbound, private subnet for RDS, **S3 Gateway Endpoint (free)** | *(changed — §10.3, no NAT gateway)* |
| CI/CD | GitHub Actions → ECR → EC2 (SSM Run Command or ECS later) | |
| Local emulation | Docker Compose + MiniStack (S3/SQS) | |

## 3.2 Service topology

```
                        ┌─────────────────────────────────────┐
   Browser ───HTTPS──►  │  EC2 #1  (t4g.large)                │
                        │   ├── Next.js 16      :3000         │
                        │   └── Django/DRF      :8000         │
                        │        └── PgBouncer  :6432         │
                        └──────┬──────────────────────┬───────┘
                               │                      │
                    enqueue    │                      │  reads/writes
              (Document.status │                      │
               → READY)        ▼                      ▼
                        ┌──────────────┐      ┌──────────────────┐
                        │ SQS          │      │ RDS PostgreSQL 17│
                        │ document-    │      │ db.t4g.medium    │
                        │ ready  (+DLQ)│      │ (private subnet) │
                        └──────┬───────┘      └────────▲─────────┘
                               │ consume               │
                               ▼                       │
                        ┌────────────────────────────────────────┐
                        │  EC2 #2  (t4g.medium)  FastAPI worker  │
                        │   stage: preprocess  (§4)              │
                        │   stage: textract    (async + SNS)     │
                        │   stage: normalize   → doc_elements    │
                        │   stage: extract     (Bedrock)         │
                        │   stage: link        (provenance)      │
                        │   stage: match       (deterministic)   │
                        │   stage: price       (deterministic)   │
                        └──────┬───────────────────────┬─────────┘
                               │                       │
                    ┌──────────▼─────────┐   ┌─────────▼──────────┐
                    │ S3 source (locked) │   │ Amazon Textract    │
                    │ S3 derived         │   │ Amazon Bedrock     │
                    │   └─ CloudFront ───┼──►│ (page rasters)     │
                    └────────────────────┘   └────────────────────┘
                                   ▲
                        SNS ◄──────┘ Textract job completion → SQS
```

**Two rules keep the hybrid from becoming two codebases that disagree** (carried forward from the Development Plan, unchanged because they are correct):

1. **Django owns the schema.** All migrations are Django migrations. The FastAPI service uses SQLAlchemy Core against Django-migrated tables — no second migration tool, no `Base.metadata.create_all`. One integration test asserts the FastAPI table definitions match the live schema, so drift fails CI rather than production.
2. **Handoff is the SQS queue, not an HTTP call.** Django enqueues on `Document.status → READY_FOR_PROCESSING` and writes a `pipeline_jobs` row; the worker consumes and advances that row through stages; Django's status endpoints read the same table. No synchronous cross-service dependency, and a worker restart loses nothing.

## 3.3 End-to-end data flow

```
 1. Estimator uploads bid-set PDF(s)  →  Django /api/projects/{id}/documents
 2. Django verifies (magic bytes, checksum, SSE, S3 version-ID) and writes
    ONCE, immutably, to the source bucket:
       projects/{project_id}/source/{document_id}/v{n}/original.pdf
    Status → READY_FOR_PROCESSING → post_save signal → SQS
 3. Worker consumes; writes pipeline_jobs(stage=PREPROCESS)
 4. PREPROCESS (§4) — validate, probe text layer, classify pages,
    hash pages, build document_manifest, split if > limits.
    Output: per-page routing decision.  NO Textract calls yet.
 5. OCR — only routed pages:
      schedule pages   → Textract AnalyzeDocument TABLES + LAYOUT
      spec/prose pages → native text layer, else DetectDocumentText
      drawing-only     → skipped (raster only, for the viewer)
    Textract reads the S3 object directly via IAM. No presigned URL.
    Completion arrives via SNS → SQS, not polling.
 6. Raw OCR JSON persisted gzipped and immutably to the derived bucket
    BEFORE any processing:  {document_id}/v{n}/ocr_result.json.gz
 7. NORMALIZE — flatten to doc_elements: page, 0–1 polygon, confidence
    scaled 0–1, table coordinates, stable element_path. Bulk COPY.
 8. EXTRACT (§5) — Claude receives scoped element batches (never pixels,
    never the whole document). Structured output, mandatory citations.
 9. LINK — citation validation + value grounding. Any field citing an
    unknown element, or whose value is not grounded in its cited text,
    is REJECTED and flagged — never repaired, never silently dropped.
    field_provenance rows written with composite confidence.
10. MATCH (§6.1) — deterministic. Rating and handing hard constraints.
    Top-N ranked candidates with per-constraint pass/fail recorded.
11. PRICE (§6.2) — deterministic, no LLM. Cost waterfall → multiplier
    + adders → margin divisor → sale/extended/subtotal/grand total.
12. REVIEW — estimator sees the grid with confidence badges; clicking a
    field opens the pre-rendered page raster with the polygon overlaid.
    Every edit writes a feedback row.
13. APPROVE → EXPORT — WeasyPrint renders the customer-facing PDF with
    standard terms and OH/KY-only tax; routed to the captured initiator.
14. Every stage emits latency, cost, and error metrics to CloudWatch.
```

## 3.4 Sizing for 10 concurrent users — corrected

Architecture v2 sized one t3.large for a two-component system. The real system has four components plus a rasterisation workload it did not account for. Corrected:

| Component | v2 selection | **v3 selection** | Rationale |
|---|---|---|---|
| API + web host | t3.large (shared with worker) | **t4g.large** (2 vCPU / 8 GiB, arm64) | Django + Next.js + PgBouncer. Graviton is ~19% cheaper for equivalent capacity. 10 concurrent sessions is comfortable on 2 vCPU because all heavy work is offloaded. |
| Pipeline worker | *(same host as API)* | **t4g.medium** (2 vCPU / 4 GiB), **separate instance** | The worker's memory profile is spiky — a 200-page plan set, PyMuPDF rasterisation, and buffered OCR JSON all peak together. Colocating it with the API means a single large bid set degrades every estimator's page loads. Separation also lets the worker be stopped, scaled, or replaced independently. **This is the most important sizing change.** |
| PostgreSQL | db.t4g.medium | **db.t4g.medium** (unchanged) | 10 estimators querying the reference library and writing quotes fits comfortably in 4 GiB. Confirmed adequate; see the burstable caveat below. |
| Connection pooling | not specified | **PgBouncer, transaction mode** | Django (4 workers) + FastAPI (async pool) + migrations + admin against one small instance. Explicit pool caps, not defaults. |
| App server | not specified | **Gunicorn + 3 Uvicorn workers** (`2 × vCPU − 1`) | OCR and LLM calls never block a request thread. |
| SQS | 15-min visibility | **15-min visibility + DLQ, `maxReceiveCount: 3`** | A poison-pill document must quarantine, not crash-loop (C6). |
| Rasterisation | *(not accounted for)* | **Pre-rendered to S3 + CloudFront** | Removes the per-click CPU spike entirely (§9 B5). This is what makes 8 GiB sufficient rather than marginal. |

### Burstable instance caveat — carried forward and sharpened

`t4g` / `t3` instances and `db.t4g` run on a **CPU credit system**: cheap at idle, throttled toward baseline once sustained load exhausts accumulated credits. Single-AZ RDS does **not** fail over.

The Architecture v2 framing — "worth monitoring for estimators working steadily through business hours" — understates the actual risk shape. Credit exhaustion will not come from steady work; it will come from **a burst of bid sets uploaded together** (an estimator clearing a morning's queue), which is exactly when the worker is CPU-bound on preprocessing and rasterisation. Mitigations, in order:

1. Alarm on `CPUCreditBalance` for both instances and the RDS instance **before** go-live, not after the first slowdown.
2. Enable **T3/T4g Unlimited** on the *worker* instance deliberately, with a billing alarm — surcharge is far cheaper than a stalled pipeline.
3. If RDS throttling is observed at peak, upgrade `db.t4g.medium` → **`db.m6g.large` Multi-AZ**, which removes the credit ceiling and adds failover.

## 3.5 Scaling path

| Trigger | Next step |
|---|---|
| Beyond ~15–20 concurrent users | RDS → `db.m6g.large` Multi-AZ; move app + worker to **ECS Fargate** with autoscaling instead of EC2 instances |
| RDS throttling at peak | `db.t4g.medium` → `db.m6g.large` Multi-AZ |
| Worker backlog grows (`ApproximateAgeOfOldestMessage` alarm) | Add worker instances — the SQS consumer is already horizontally safe given per-document idempotency keys |
| Strict ordering or exactly-once required | **Only then** consider SQS FIFO. Retries and redrive are already handled by standard SQS + DLQ (C6) |
| > 1M pages/month | Negotiate AWS committed-use for Textract; consider Textract Queries or a custom adapter for recurring plan formats |
| Multi-region / DR | S3 Cross-Region Replication + RDS read replica in a secondary region |
| NFR-4 hardening required by CBC IT | VPC **interface** endpoints for Textract, Bedrock, SSM, and CloudWatch so traffic never traverses the public internet (S3 already uses a free gateway endpoint) |

---

# 4. Document preprocessing

**None of the five source documents contained a preprocessing stage.** All of them went from "upload PDF" straight to "call Textract on the document." For invoices that is fine. For architectural bid sets it is the root cause of C17 (NFR-6 unreachable) and of the largest single line on the projected AWS bill.

The shape of the problem: a bid set is **40–200+ pages** of architectural drawings, of which typically **3–8 pages** contain the door schedule, frame schedule, and Division 08 hardware schedule. Running `AnalyzeDocument` with `TABLES` across all of them costs 25–50× more than necessary and takes minutes instead of seconds. Preprocessing exists to answer one question per page — *does this page need structured OCR, cheap OCR, or nothing?* — before spending a cent.

## 4.1 Stage contract

**Input:** the immutable source PDF in S3 (never mutated).
**Output:** a `document_manifest` row per page, persisted **before** any OCR call, plus derived artefacts in the derived bucket.
**Invariant:** the source PDF is read-only. Every preprocessing output — rasters, split parts, extracted text — lands in `derived/`, never `source/`.

The manifest is not bookkeeping. It is the audit answer to *"why didn't the system read page 47?"* — which NFR-3 will eventually require someone to answer.

```
document_manifest(
  id, document_id, page_number,
  page_hash          text,     -- sha256 of normalised page content stream
  width_pt, height_pt, rotation,
  text_layer         enum(RICH, SPARSE, NONE, VECTOR_OUTLINED),
  native_word_count  int,
  vector_path_count  int,
  page_class         enum(DOOR_SCHEDULE, HARDWARE_SCHEDULE, FRAME_SCHEDULE,
                          FINISH_SCHEDULE, SPEC_TEXT, DRAWING, TITLE, INDEX,
                          UNKNOWN),
  class_confidence   numeric(5,4),
  class_method       enum(BOOKMARK, TITLE_BLOCK, KEYWORD, MODEL, MANUAL),
  ocr_route          enum(TEXTRACT_TABLES, TEXTRACT_TEXT, NATIVE_TEXT, SKIP),
  raster_key         text,     -- pre-rendered page image in derived bucket
  ocr_cost_estimate  numeric,
  created_at
)
```

## 4.2 Step 1 — Validate and probe the text layer

Before anything else, three cheap checks:

| Check | Tool | Action on failure |
|---|---|---|
| Magic bytes, not just extension | already implemented in intake | Reject at upload (existing behaviour, keep) |
| Encrypted / password-protected | `pikepdf.open()` → `PasswordError` | Fail the job with a specific status; the estimator can supply the password or re-export |
| Structurally corrupt / linearisation damage | `pikepdf.open(..., allow_overwriting_input=False)` then `save()` to a repaired copy in derived | Repair to derived, proceed against the repaired copy, **flag it** — the source stays untouched |

Then probe every page for a text layer with PyMuPDF. This determines the entire downstream route and costs nothing:

```python
import fitz  # PyMuPDF

def probe_page(page: fitz.Page) -> dict:
    words   = page.get_text("words")          # (x0,y0,x1,y1,word,block,line,word_no)
    drawings = page.get_drawings()            # vector paths
    return {
        "native_word_count": len(words),
        "vector_path_count": len(drawings),
        "rotation": page.rotation,
        "width_pt": page.rect.width,
        "height_pt": page.rect.height,
    }
```

Four outcomes, and the third one is the trap:

| Outcome | Signal | Route |
|---|---|---|
| **RICH** | Many words, coherent bounding boxes | Native text extraction. Zero OCR cost. Still route schedule *tables* to Textract, because word positions alone do not give cell/row/column structure. |
| **SPARSE** | Few words, mostly a title block | Scanned or raster export → OCR required |
| **NONE** | Zero words | Scanned → OCR required |
| **VECTOR_OUTLINED** | **Zero or near-zero words but a very high vector path count** | **The trap.** Architectural PDFs are frequently exported with text converted to vector outlines. `get_text()` returns nothing, so a naive pipeline concludes "scanned" — but the page is not a raster, it is thousands of filled paths. It must be **rasterised at higher DPI before OCR**, because OCR of a downsampled vector-outlined sheet loses the small annotation text where door numbers and ratings live. Detection rule: `native_word_count < 20 and vector_path_count > 500`. |

> This branch matters more than it looks. Vector-outlined text is common in the exact document class this system targets, and getting it wrong produces an empty extraction with a *high* OCR confidence score — the worst possible failure mode under NFR-2.

## 4.3 Step 2 — Page classification (the money step)

Classify every page into `page_class` using the cheapest method that resolves it. Escalate only when cheaper methods fail. Record which method was used — `class_method` is what lets you measure whether the expensive tier is earning its keep.

**Tier 1 — PDF outline / bookmarks (free, instant).** Architectural sets are usually bookmarked by sheet. Match outline titles against a keyword set.

**Tier 2 — Title block and sheet number (free).** Sheet numbers follow conventions (`A6.xx` for schedules in many offices, `A0.xx` for index). The title block text sits in a predictable corner region — extract the bottom-right ~15% of the page and keyword-match.

**Tier 3 — Full-page keyword match on the native text layer (free, RICH pages only).** Search for anchors:

```python
SCHEDULE_ANCHORS = {
    "DOOR_SCHEDULE":     ["DOOR SCHEDULE", "DOOR AND FRAME SCHEDULE", "OPENING SCHEDULE"],
    "HARDWARE_SCHEDULE": ["HARDWARE SCHEDULE", "HARDWARE SETS", "HARDWARE GROUP",
                          "DIVISION 08", "SECTION 08 71 00"],
    "FRAME_SCHEDULE":    ["FRAME SCHEDULE", "FRAME TYPES"],
    "FINISH_SCHEDULE":   ["FINISH SCHEDULE", "ROOM FINISH SCHEDULE"],
}
```

Anchor matching must be **whitespace- and case-insensitive** and tolerate letter-spaced titles (`D O O R   S C H E D U L E` is common in CAD title text).

**Tier 4 — Cheap model classification (only for unresolved pages).** Render a low-DPI thumbnail (~100 DPI, greyscale) and ask **Claude Haiku** to classify it. This is the only paid step in classification and it runs on a small minority of pages. Batch several thumbnails per call.

**Tier 5 — Manual.** Unresolved pages default to `UNKNOWN` and are surfaced in the review UI as *"pages the system did not read."* An estimator can mark a page as a schedule, which reprocesses just that page and writes a `feedback` row (FR-13) that improves Tier 1–3 anchors over time.

> **Design rule: never silently skip.** `SKIP` in `ocr_route` must always be visible in the UI with its reason. A page the system decided not to read is exactly the kind of silent omission NFR-2 forbids. The estimator's ability to say *"read page 47 anyway"* is a required feature, not a nice-to-have.

## 4.4 Step 3 — OCR routing

| `page_class` | `ocr_route` | API | Cost / 1,000 pages | Why |
|---|---|---|---|---|
| `DOOR_SCHEDULE`, `HARDWARE_SCHEDULE`, `FRAME_SCHEDULE`, `FINISH_SCHEDULE` | `TEXTRACT_TABLES` | `AnalyzeDocument` `TABLES` + `LAYOUT` | **$15** | Cell/row/column structure is the whole point. Layout is included at no extra charge when Tables is enabled. |
| `SPEC_TEXT` with RICH text layer | `NATIVE_TEXT` | PyMuPDF | **$0** | Word positions and reading order are already in the file |
| `SPEC_TEXT` without a text layer | `TEXTRACT_TEXT` | `DetectDocumentText` | **$1.50** | 10× cheaper than Tables; prose has no cells |
| `DRAWING`, `TITLE`, `INDEX` | `SKIP` | — | **$0** | Raster only, for the viewer. Reconsider only if CBC confirms that ratings live in margin notes (Open Item 9) |
| `UNKNOWN` | `SKIP` + flag | — | **$0** | Surfaced for estimator decision |

**Worked example.** A 200-page plan set with 6 schedule pages and 24 spec pages:

| Approach | Calculation | Cost | OCR wall time |
|---|---|---|---|
| Naive (all pages, Tables) | 200 × $0.015 | **$3.00** | minutes |
| Triaged | 6 × $0.015 + 24 × $0.0015 (+170 skipped) | **$0.13** | seconds |

That is a **23× reduction on the dominant cost line** and it is what makes NFR-6 reachable. Across the projected 150 bid sets / 3,000 pages per month it is the difference between ~$45/month and ~$7/month on Textract (§10.3).

> ⚠ **Open Item 9 dependency.** If CBC answers that fire ratings sometimes live in **drawing margin notes** rather than the schedule, the `DRAWING` route must change from `SKIP` to `TEXTRACT_TEXT` for pages adjacent to schedules. Build the routing table as **configuration**, not as `if` statements, so this is a config change rather than a code change. This is the concrete form of Risk R1's "make the extraction hint configuration, never a hardcoded column index."

## 4.5 Step 4 — Rasterisation policy

Render each page **once**, at ingest, to the derived bucket. Do not render on demand (§9 B5).

| Purpose | DPI | Format | Key |
|---|---|---|---|
| Classification thumbnail | 100, greyscale | JPEG q70 | `{document_id}/v{n}/thumb/{page}.jpg` |
| Review viewer | 150 | WebP (PNG fallback) | `{document_id}/v{n}/page/{page}.webp` |
| OCR input for `VECTOR_OUTLINED` pages | **300** | PNG | `{document_id}/v{n}/ocr-input/{page}.png` |

Notes that matter:

- **Honour `page.rotation`.** A rotated sheet rendered without applying rotation produces polygons that overlay 90° off. This is the single most common cause of "the highlight is in the wrong place," and the Phase 1 verification explicitly tests a rotated sheet for this reason.
- **Oversized sheets.** Arch D/E sheets (24×36", 30×42") at 300 DPI produce very large rasters. Cap the long edge at ~4,000 px for the viewer tier; use full DPI only on the `ocr-input` tier, and only for pages actually routed to OCR.
- **Rasters are cache-warm assets.** Serve through CloudFront with a long `max-age` — the underlying source is immutable, so cache invalidation is never needed.

## 4.6 Step 5 — Splitting for API limits (resolves C16)

Textract async accepts **500 MB / 3,000 pages** per document. Combined plan sets exceed this. Both source documents quoted the limit; neither handled it.

Splitting is straightforward; **preserving provenance across the split is not.** Rules:

1. Split on **page boundaries only**, into parts of ≤ 1,000 pages (well under the limit, and it bounds worst-case retry cost).
2. Record `page_offset` per part in the manifest.
3. **Normalisation converts every part-local page number back to the document-global page number before writing `doc_elements`.** A citation must always point at a page number that means something in the original PDF the estimator is looking at.
4. `element_path` (§7.2) is built from the **global** page index, so re-running normalisation after a different split still produces identical element identities. This is what keeps splitting idempotent.

In practice, triage makes splitting rare — you are OCR-ing 6 pages, not 3,000. But the guard must exist, because a single oversized document that silently fails is a worse outcome than a slow one.

## 4.7 Step 6 — Page hashing for addendum diffing

Hash each page's normalised content stream. When an addendum arrives as a new document covering the same sheets:

- Pages whose hash is **unchanged** reuse their existing `doc_elements` and their existing extraction — **no OCR call, no LLM call, no cost**.
- Pages whose hash **changed** are reprocessed and their openings marked for re-review.
- Pages present in one version and not the other are reported as added or removed.

This turns "an addendum arrived" from a full reprocess into a diff. It is directly load-bearing for FR-14 and it is cheap to build now — the hash is computed during the manifest pass regardless. **Build the hash and the diff report; build no reconciliation logic** until Open Item 11 is answered (Risk R2).

## 4.8 Preprocessing checklist

- [ ] Source PDF never mutated; all outputs land in `derived/`
- [ ] Encryption and corruption detected and reported, not crashed on
- [ ] Text layer probed on every page; `VECTOR_OUTLINED` detected explicitly
- [ ] Every page classified; `class_method` recorded
- [ ] `document_manifest` persisted **before** the first Textract call
- [ ] Routing table is configuration, not code
- [ ] `SKIP` decisions visible in the UI with a reason and an override
- [ ] Rotation applied at raster time
- [ ] Rasters pre-rendered once, served via CloudFront
- [ ] Split parts carry `page_offset`; global page numbers written to `doc_elements`
- [ ] Page hashes stored for addendum diffing
- [ ] Estimated OCR cost per document logged **before** the spend (enables a budget guard)

---

# 5. Extraction pipeline and best practices

## 5.1 The traceability contract

This is the load-bearing principle of the whole system and it is stated once, here, in its final form:

> **Textract produces deterministic geometry. The model produces semantic interpretation and must cite Textract-normalised element IDs for every field it emits. A field whose citation cannot be validated is rejected, not repaired. "Show me the source" is a database join, never a second inference.**

Everything in §5 exists to enforce that sentence. The reason it is worth the engineering is FR-9: the estimator must be able to **verify**, not merely trust. A confidence score without a clickable source is a number nobody has a reason to believe.

## 5.2 Why Textract first, Claude second

- Textract's bounding boxes and per-element confidence are exactly what pixel-level traceability requires, and it is deterministic and cheap relative to LLM calls. **Language models cannot report literal pixel coordinates** and should never be asked to.
- Claude handles what Textract cannot: semantic interpretation of a real-world, inconsistent door schedule — abbreviations, merged and split cells, cross-references between the door schedule and the hardware schedule, inferring alternate/addendum status from surrounding notes.
- Keeping the responsibilities separate, and forcing the model to **cite** rather than re-emit text, is what prevents hallucinated fields from entering a priced quote.

## 5.3 Two-pass extraction: locate, then extract

Do not send a document to the model and ask for openings. Two passes, different models:

**Pass A — Locate (Claude Haiku).** Input: the table-block inventory from Textract for classified schedule pages — table IDs, dimensions, header-row text only. Output: which tables are the door schedule, the frame schedule, the hardware-set definitions, and which are irrelevant (finish legends, general notes, revision blocks). Cheap, small context, and it prevents Pass B from ever seeing an irrelevant table.

**Pass B — Extract (Claude Opus).** One call **per table**, not per document. Input: that table's cells with their `element_id`s, its header row, and a bounded window of surrounding text elements (notes and legends adjacent to the table on the same page). Output: structured opening records with citations.

Batching per table rather than per document is what keeps context small, cost predictable, and failures isolated — one malformed table fails one call, not the whole bid set.

## 5.4 Structured output and the system prompt

Enforce structure with tool use / JSON schema mode so free text is impossible. The schema is the contract; prose is not accepted.

```
You are extracting a door/opening schedule from an OCR analysis result.
You will be given structured elements, each with element_id, text, and
table position (row, column, header flag).

For every opening, return one record with these fields:
door_number, size, handing, finish, fire_rating, hardware_group,
alternate_designation.

Rules, in priority order:

1. For EVERY field you populate you MUST return the element_id(s) of the
   exact source cells or words the value came from. Cite only ids present
   in the input. Never invent an id.
2. Return values EXACTLY as written in the source. Do not normalise,
   expand, convert, or correct them. "3070" stays "3070". "90 MIN" stays
   "90 MIN". Normalisation happens downstream.
3. If a field is not present, return null with an empty citation list.
   Never guess, never infer from a neighbouring opening, never carry a
   value down from the row above unless the source itself does so with an
   explicit ditto mark — and if it does, cite the ditto mark's element.
4. If a cell is ambiguous, illegible, or spans merged rows in a way you
   cannot resolve, return null and set needs_review = true with a reason.
5. Report your own confidence per field as a number in [0,1].
```

Rule 2 is the one that is easy to get wrong and expensive to get wrong. **The model must not normalise.** If it returns `3'-0" x 7'-0"` instead of `3070`, the value can no longer be grounded against the cited cell text (§5.6), and a deterministic parser that is correct 100% of the time has been replaced by a model that is correct most of the time. Extraction returns raw strings; §5.7 owns interpretation.

Rule 3 exists because carried-down values are a real pattern in door schedules and a real hazard: a model that helpfully propagates a fire rating down a column has just invented a code-compliance claim.

**Inference parameters:** `temperature = 0`, fixed `top_p`, fixed `max_tokens`. Record model ID, prompt version, and every parameter on the `extraction_runs` row. An extraction that cannot be reproduced cannot be audited.

## 5.5 Response shape

```json
{
  "opening_id": "D-101",
  "needs_review": false,
  "fields": {
    "door_number":  {"value": "101",    "source_element_ids": ["el_4471"], "confidence_llm": 0.98},
    "size":         {"value": "3070",   "source_element_ids": ["el_4472"], "confidence_llm": 0.95},
    "handing":      {"value": "LH",     "source_element_ids": ["el_4475"], "confidence_llm": 0.90},
    "finish":       {"value": "US26D",  "source_element_ids": ["el_4478"], "confidence_llm": 0.88},
    "fire_rating":  {"value": "90 MIN", "source_element_ids": ["el_4210"], "confidence_llm": 0.72},
    "hardware_group": {"value": "HW-3", "source_element_ids": ["el_4480"], "confidence_llm": 0.91},
    "alternate":    {"value": null,     "source_element_ids": [],          "confidence_llm": null}
  }
}
```

`source_element_ids` → join `doc_elements` → `page_number` + `polygon` → overlay a highlight box on the pre-rendered page raster. That is the entire "trace the word to the plan" feature, and it involves no inference.

## 5.6 Validation gate — two checks, not one

Every source document specified citation-existence validation. **None specified value grounding, and that is a real hole.** A model can cite a perfectly valid `element_id` and still emit a value that does not appear in it — the citation passes, the value is fabricated, and the estimator sees a confident wrong answer with a clickable source that does not say what the system claims it says. That is worse than no citation at all.

Both checks are mandatory:

```python
def validate_field(field, supplied_elements: dict[str, str]) -> Verdict:
    # Check 1 — citation existence (specified in the source documents)
    unknown = [e for e in field.source_element_ids if e not in supplied_elements]
    if unknown:
        return Verdict.REJECT("cited element_id not in supplied set", unknown)

    if field.value is None:
        return Verdict.ACCEPT_NULL() if not field.source_element_ids \
               else Verdict.REJECT("null value with non-empty citation")

    if not field.source_element_ids:
        return Verdict.REJECT("value with no citation")

    # Check 2 — value grounding (NEW; the gap in every source document)
    cited_text = " ".join(supplied_elements[e] for e in field.source_element_ids)
    if not grounded(field.value, cited_text):
        return Verdict.REJECT("value not grounded in cited element text")

    return Verdict.ACCEPT()
```

`grounded()` is a normalised containment test — case-folded, whitespace-collapsed, punctuation-stripped — with a similarity floor (`rapidfuzz.partial_ratio ≥ 90`) to tolerate OCR noise and hyphenation, and **never** a semantic comparison. It is checking that the string is *there*, not that it *means the same thing*.

**Rejection behaviour, non-negotiable:**

- Rejected fields are **flagged for estimator review**, never repaired, never silently dropped, never retried into acceptance.
- One schema-repair retry is permitted **only** for output that fails JSON-schema validation (malformed structure), passing the validator error back. Never for a semantic rejection. Never loop.
- Rejection counts per prompt version are a monitored metric. A rising rejection rate is the earliest warning that a prompt change or a model version bump has degraded quality.

**Phase 2's critical negative test** — feed the validator a fabricated `element_id` and assert the field is rejected and flagged, not persisted — remains the single most important test in the suite. Add a second: feed a valid `element_id` with a value that does not appear in it, and assert the same.

## 5.7 Deterministic post-parsers — the model proposes, code disposes

Every field with a known format is parsed by code, not by the model. The model returns the raw string; a deterministic parser produces the typed value; **both are stored**.

| Field | Raw | Parser | Typed output | Failure |
|---|---|---|---|---|
| Size | `3070` | Fixed 4-digit rule: first two digits width, last two height, feet-inches | `width_inches=36`, `height_inches=84` | Non-conforming → `needs_review`, typed fields null, raw preserved |
| Finish | `US26D` | Lookup in `finish_codes` (both US and BHMA systems) | `finish_code_id` | Unrecognised → flag, never fuzzy-match to the nearest code |
| Fire rating | `90 MIN`, `90`, `1-1/2 HR`, `B LABEL` | Regex + tier table → {20, 45, 60, 90} | `fire_rating_minutes=90` | Unrecognised → flag; **never default to unrated** |
| Handing | `LH`, `L.H.`, `LHR` | Canonical enum map | `LH` / `RH` / `LHR` / `RHR` | Unrecognised → flag |
| Throat depth | wall type text | `throat_depths` lookup | `throat_depth_id` | Outside the five standards → custom manual entry |

Why this split matters: the 4-digit size notation is a **fixed CBC parsing rule**, not something to be inferred per document. A regex is right 100% of the time. A model is right most of the time, cannot be unit-tested, and costs money. Anywhere a rule exists, the rule wins.

## 5.8 Zero-tolerance fields: fire rating and handing

These two get a stricter path than everything else, because getting them wrong is a code-compliance and functional failure rather than a pricing error.

| Rule | Rationale |
|---|---|
| **Never infer from a sibling opening.** A rating or handing absent on this row is absent, full stop. | Column-carry inference invents safety claims |
| **`fire_rating_absent` is an explicit boolean**, distinct from "not yet extracted" and from null | FR-8 requires flagging *missing* ratings; a null cannot distinguish the three states |
| **Never auto-accept below threshold.** Regardless of composite confidence, a rating or handing below the review threshold requires explicit estimator confirmation before it reaches a quote line | NFR-2: never silently guessed |
| **A rated opening never matches an unrated catalogue item** — hard constraint in matching, regardless of text similarity | Rated hardware is a distinct certified product line, not a spec note |
| **A handed opening never matches a SKU of the opposite hand** — hard constraint | Handed parts are separate SKUs |
| **Build no rating→price rule** until CBC answers which categories are rating-sensitive | Risk R1: the half of Open Item 9 that nobody discusses |
| **Log every rating and handing decision** with its source element and page | These are the fields an audit will ask about |

Where a rating is found *outside* the door schedule (a margin note, the frame schedule), the extraction must record **which** location it came from. That data, accumulated over the first few real bid sets, is the empirical answer to Open Item 9 — the system can answer CBC's open question by observing it.

## 5.9 Confidence: composition and calibration

```
ocr_confidence   = min(confidence of all cited elements)   # Textract 0-100, scaled to 0-1
llm_confidence   = model self-report
completeness_penalty = f(fields_populated / fields_expected)   # stored, not just applied
final_confidence = min(ocr_confidence, llm_confidence) * completeness_penalty
```

Properties this guarantees, all of which are asserted in tests:

- `final_confidence` can never exceed either input. A confident model reading a blurry cell does not produce a confident result.
- Every component is **stored**, not just the product, so a score can be explained rather than merely displayed.
- A missing expected field drags the score down through the penalty rather than being invisible.

**Calibration replaces the placeholder 0.80** (C14). The threshold is not a taste judgment; it is a measured trade-off between review burden and escaped errors:

1. Build the golden set (§5.10). Extract with the threshold disabled.
2. For each candidate threshold from 0.50 to 0.99 in steps of 0.01, compute **flagged rate** (share of fields sent to review) and **escape rate** (share of *unflagged* fields that are wrong).
3. Plot both. Choose the threshold where escape rate crosses the tolerance CBC sets — and **make CBC set it**, in terms they own: *"at this setting the system will flag 18% of fields for review and will let through roughly 1 wrong field per 400."*
4. Set **per-field** thresholds, not one global number. Fire rating and handing warrant a stricter threshold than hardware group, because their cost of error is categorically different.
5. Re-calibrate on every prompt or model version change. Store the calibration run alongside the prompt version.

## 5.10 Evaluation harness — the thing that makes prompt changes safe

Without this, every prompt edit is a guess and every model upgrade is a risk nobody can size.

**Golden set.** CBC has provided 3 bid sets. Build a labelled ground-truth set from them plus, critically, **at least one deliberately messy document** (Q9): merged cells, a rating in a margin note, a rotated sheet, an addendum revision, a vector-outlined sheet. The clean cases prove the system works; the messy one is where it will actually fail.

Labels are stored as field-level ground truth per opening, including **expected nulls** — "this opening genuinely has no fire rating" is a labelled fact, not an absence of a label.

**Metrics, reported per field, not aggregated:**

| Metric | Why |
|---|---|
| Precision | Of the values produced, how many were right |
| Recall | Of the values present in the document, how many were found |
| **Absent-accuracy** | Of the fields genuinely absent, how many were correctly reported absent rather than hallucinated. **This is the metric NFR-2 actually cares about** and it is invisible in a precision/recall summary |
| Citation validity | Share of cited elements that exist and ground the value |
| Escape rate at threshold | Wrong values that were *not* flagged |
| Cost and latency per document | Guards against a prompt change that quietly triples spend |

**CI gate:** a prompt or model change that reduces any per-field metric below its recorded baseline fails the build. Prompts are versioned artefacts in the repository (`backend/pipeline/llm/prompts/extraction/v3.md`), referenced by `extraction_runs.prompt_version`, and never edited in place.

**Feedback loop (FR-13):** every estimator correction writes a `feedback` row with before/after values, the field, the extraction run, and the user. That table is simultaneously the tuning dataset, the source of new golden-set cases, and the empirical answer to several of CBC's open items.

## 5.11 Cross-schedule resolution

A door schedule says `HW-3`. The Division 08 spec section defines what `HW-3` contains. Joining them is a **separate call with its own narrow context** — a different task with different failure modes, and mixing it into opening extraction degrades both.

- Input: the hardware-set definition block only, plus the set of group callouts observed in the door schedule.
- Output: `hardware_group → [component records]`, each component citing its own elements.
- Unresolved groups (callout present, definition not found in the document) are **flagged, never guessed** — a hardware set invented from the model's general knowledge of what an `HW-3` usually contains is precisely the failure NFR-2 prohibits.
- Where the architect specified an explicit manufacturer part or series instead of a named set, that is not a resolution failure — it is the normal case (§1.3) and flows straight to matching.

## 5.12 Cost and latency controls

| Control | Effect |
|---|---|
| Page triage before OCR (§4) | 20–25× on the dominant cost line |
| Model tiering — Haiku for classification and table location, Opus only for schedule interpretation | Large reduction in premium-model tokens |
| Table-scoped batching | Bounded context per call; predictable cost per opening |
| **Prompt caching** on the static prefix (system prompt, finish-code table, few-shot examples) | Cached input tokens are billed at roughly a tenth of the standard rate; the cache write costs about 1.25×, so a prefix reused twice or more within the TTL is already ahead. The prefix must be **≥ 1,024 tokens** to be cacheable and must be **byte-identical** across calls — put it first, and never interpolate anything variable into it |
| Page-hash reuse on addenda (§4.7) | Unchanged pages cost nothing to reprocess |
| Idempotency keys on OCR and LLM calls | A retry storm cannot double-bill |
| Per-document cost estimate logged before spend | Enables a hard budget guard per bid set |

> ⚠ **Bedrock Batch inference is not available to this path.** Batch offers a 50% discount, but current reporting indicates it does not support tool calling or structured output — which the extraction contract requires. Verify against Bedrock documentation before planning around it. Batch **is** usable for offline work that does not need structured output: evaluation runs, bulk re-extraction after a prompt change, and backfills. Use it there.

## 5.13 Extraction checklist

- [ ] Model never receives raw pixels in the normal path
- [ ] Model never receives a whole document — table-scoped batches only
- [ ] Structured output enforced by schema; free text impossible
- [ ] Model returns raw source strings; it does not normalise
- [ ] Citation existence validated against the supplied element set
- [ ] **Value grounding validated against cited element text**
- [ ] Rejections flagged, never repaired; one schema-repair retry maximum
- [ ] Deterministic parsers own every known format
- [ ] `*_absent` booleans distinguish "absent" from "not extracted"
- [ ] Fire rating and handing follow the zero-tolerance path
- [ ] Composite confidence stored component-wise
- [ ] Thresholds calibrated per field against a measured curve, not chosen
- [ ] Golden set with a messy document; per-field metrics including absent-accuracy
- [ ] Prompts versioned; CI gates on metric regression
- [ ] `temperature=0`; model ID, prompt version, and parameters recorded per run
- [ ] Prompt caching on a stable, byte-identical prefix

---

# 6. Matching and pricing engines

## 6.1 Matching engine (FR-4)

Deterministic and explainable. No LLM in the accept/reject decision — a match the estimator cannot interrogate is a match they will not trust, and NFR-2 forbids silent guessing.

**Constraint model — hard constraints filter, soft constraints score:**

| Constraint | Type | Behaviour |
|---|---|---|
| Fire rating | **Hard** | A rated opening never matches an unrated item, regardless of text similarity. An unrated opening may match a rated item only with an explicit flag (over-specification is a cost issue, not a safety one) |
| Handing | **Hard** | `LH` opening never matches an `RH`-only SKU |
| Finish | **Scored** | Exact code match scores highest; same base metal scores lower; different base metal scores near zero. **US19 and US26D never collapse** |
| Size | Scored | Exact match, then nearest standard, then custom |
| Product type / CSI division | **Hard** | A Division 10 accessory never matches a Division 08 opening |
| Vendor / series | Scored | Specified vendor scores highest; direct-equal candidates score lower and are marked `is_direct_equal` |
| Stock status | Scored | `is_stock` items preferred, per NR-13's "automate the stock, manual beyond" principle |

**Output:** top-N ranked candidates, each with `match_confidence` **and per-constraint pass/fail stored individually** (`rating_ok`, `handing_ok`, `finish_ok`). A rejected match must explain *which* constraint failed, not merely score low. This mirrors the estimator's existing behaviour that CBC explicitly validated: *"here are 3 close matches — is it one of these?"*

**Manual cut-off (NR-13).** Below a configurable match-confidence cutoff, or for heavily customised items (non-stock sizes, unusual preps, discontinued options), route to the **manual / custom-RFQ path** (FR-16) rather than auto-proposing a line. Do not attempt to price every option permutation. The estimator owns the long tail by design, not by failure.

**Direct-equal substitution** is recorded, never decided. When the system proposes a substitute it sets `is_direct_equal = true` and leaves `substitution_note` for the estimator. Choosing an equal is judgment (§1.4).

## 6.2 Pricing engine (FR-5, FR-6, FR-7)

**Fully deterministic. There is no LLM call anywhere in the pricing path.** CBC's logic is completely specified and must be replicated exactly, not approximated.

**Step 1 — Cost waterfall, in strict priority order:**

```
1. P21_LAST_PO       — if sold within 12 months and no intervening price increase
2. DISTRIBUTOR_SHEET — Banner/SecLock (Allegion), Pionite/Wilsonart (laminate)
3. MFR_LIST          — manufacturer list price
4. VENDOR_RFQ        — live quote requested; line state AWAITING_RFQ
5. MANUAL            — first-class from day one, not a fallback (Risk R3)
```

Every line records which path produced its cost (`cost_source`) and the cost's `effective_date`. Costs older than the configured freshness window (6–8 months, **configurable, not hardcoded**) set `cost_is_stale` and surface NR-2's *"price may be out of date — refresh"* prompt. **No automatic silent refresh** — a price that changes underneath an estimator without their knowledge is exactly the stale-data failure NFR-10 is about.

> **Risk R3 in practice.** P21 item IDs diverge from manufacturer part numbers and semi-custom items will not match cleanly. Therefore: `catalog_items.p21_item_id` is **nullable on purpose**; `MANUAL` is a first-class `cost_source` from day one; the system **never auto-accepts a cost match on part-number string similarity alone**; and the P21 record that was matched is always surfaced so the estimator can reject it.

**Step 2 — List × multiplier** for non-special-priced items: `cost = list_price × multiplier`, where the multiplier comes from `vendor_multipliers` keyed by vendor, tier, and effective date. The `source_sheet_version` is stored on the line, because NFR-3 requires traceability to the multiplier tier *and* sheet version that produced it. **MAP is never used as cost.** Manual adders (electrification, non-removable-pin hinges, premium and lead-time finishes — NR-4) are applied on top of the base price book.

**Step 3 — Margin as divisor**, per product-type band, editable per line:

```
sale_each = our_cost / (1 - margin_pct)
extended  = sale_each * quantity
```

An override records `margin_overridden = true` and a `margin_override_reason` (e.g. the confirmed sourcing-driven Wendy's case). If the resulting margin falls below the band floor, set `below_floor_flag`. **Build the flag; build no approval workflow** (C13).

**Step 4 — Assembly (FR-7):** grouped by door with subtotals, a separate restroom-accessories block, and a freight line that renders `TBD` unless an estimator enters a value (C11). Sales tax applies **only** to Ohio and Kentucky, from `tax_rates` reference data with effective dates — never from constants.

**Step 5 — Stored, not computed on read.** `sale_each`, `extended`, and `subtotal` are **persisted**. A quote issued in March must reproduce identically in September after the margin sheet and multiplier sheets have both changed. Recomputing on read silently rewrites history.

**Golden-file test (Phase 4):** given a known quantity, cost, and band from a real CBC worked example, assert `sale_each = cost / (1 - margin)` and that subtotals and grand total match **to the cent**. Assert the waterfall honours priority order. Assert a stale cost sets the flag. Assert an override records a reason.

---

# 7. Data model

Postgres 17. `uuid`, `numeric`, and native types throughout. All tables carry `created_at` / `updated_at`. Table names reflect the §0.2 C9 rename — `doc_elements`, not `di_elements`.

## 7.1 Deltas to existing tables

**`projects_project`** — this is the "bid." A Project is the bid set; a Document is one PDF within it.

| Column | Type | Purpose |
|---|---|---|
| `source_channel` | enum `EMAIL` / `MANUAL` / `PHONE` | FR-1; NR-5 covers phone-in |
| `initiator_email` | text | FR-10 — routes back to the specific salesperson, never a group inbox |
| `initiator_user_id` | uuid FK → user, nullable | when the initiator is a known internal user |
| `rfp_body_text` | text | unstructured RFP context from the intake email |
| `brand`, `architect`, `general_contractor` | text, nullable | FR-11 prior-quote lookup keys |

**`projects_document`** — no structural change; add only:

| Column | Type | Purpose |
|---|---|---|
| `ocr_result_key` | text, nullable | pointer to the persisted raw OCR JSON *(renamed from `di_result_key`)* |
| `ocr_result_version_id` | text, nullable | S3 version, so a re-run is distinguishable from an overwrite |
| `page_count` | integer, nullable | from the manifest |
| `manifest_complete` | boolean | preprocessing finished before OCR was attempted |

The existing `DOCUMENT_ROLE_ADDENDUM` role is retained — but see Risk R2 for why it is **not, by itself, an answer to FR-14**.

## 7.2 Preprocessing and provenance chain

**`document_manifest`** — one row per page. Schema in §4.1. Persisted before the first OCR call. Unique on `(document_id, page_number)`.

**`doc_elements`** — every word, line, table cell, and selection mark from OCR.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | the `element_id` the model cites |
| `document_id` | uuid FK | keyed to the document, not the bid — a bid set can be several PDFs |
| `element_path` | text | positional pointer, e.g. `pages/3/words/412`, `tables/0/cells/17`. **Natural key.** Textract mints a fresh `Block.Id` on every job, so positional paths are what make normalisation idempotent and let a re-run re-link deterministically instead of orphaning citations *(renamed from `di_path`)* |
| `page_number` | integer | **document-global**, after any split-part offset is applied (§4.6) |
| `element_type` | enum `word` / `line` / `table_cell` / `selection_mark` | |
| `text` | text | |
| `x0,y0,x1,y1,x2,y2,x3,y3` | `real` ×8 | polygon vertices as 0–1 page fractions. **Eight columns, not JSONB** (§9 B4) |
| `bbox_x_min,y_min,x_max,y_max` | `real` ×4 | derived, indexed — supports spatial queries without unpacking the polygon |
| `ocr_confidence` | `real`, nullable | scaled 0–1 from Textract's 0–100. **Never recomputed, never overwritten** *(renamed from `di_confidence`)* |
| `reading_order` | integer | preserves resolved reading order |
| `table_id` | uuid, nullable | groups cells of one table |
| `row_index`, `col_index` | integer, nullable | 0-indexed (Textract is 1-indexed; normalise down) |
| `column_header` | boolean, nullable | |

Constraints: `unique (document_id, element_path)`. Indexes: `(document_id, page_number)`, `(table_id)`. Expect tens of thousands of rows per 40–80 page bid set — with triage (§4), far fewer. Partition only if measurement says so.

**`extraction_runs`** — so a re-extraction never clobbers a prior one an estimator has already reviewed.

| Column | Type |
|---|---|
| `id` | uuid PK |
| `document_id` | uuid FK |
| `model_id` | text — the **resolved** Bedrock model/inference-profile ID (C5) |
| `prompt_version` | text |
| `inference_params` | jsonb — temperature, top_p, max_tokens |
| `ocr_result_version_id` | text |
| `started_at`, `completed_at`, `status` | |

**`field_provenance`** — field → source elements → page + polygon, with composite confidence.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `extraction_run_id` | uuid FK | |
| `opening_id` | uuid FK → openings, nullable | nullable so non-opening extractions reuse the mechanism |
| `field_name` | text | e.g. `fire_rating`, `handing` |
| `extracted_value` | text, nullable | null is a legitimate result — "not present in the schedule" |
| `ocr_confidence` | `real`, nullable | min across cited elements |
| `llm_confidence` | `real`, nullable | model self-report |
| `completeness_penalty` | `real` | **stored, not just applied**, so the score is auditable |
| `final_confidence` | `real`, nullable | `min(ocr, llm) × penalty` |
| `grounding_score` | `real`, nullable | value-grounding similarity (§5.6) |
| `page_number` | integer, nullable | denormalised for cheap grid reads (§9 B12) |
| `bbox_x_min,y_min,x_max,y_max` | `real` ×4, nullable | denormalised union of cited element boxes — the grid never joins |
| `review_state` | enum `AUTO` / `FLAGGED` / `CONFIRMED` / `CORRECTED` / `REJECTED` | drives FR-8 flagging and FR-9 approval |
| `rejection_reason` | text, nullable | why the validation gate rejected it |

**`field_provenance_elements`** — join table. `field_provenance_id` (FK, `ON DELETE CASCADE`), `doc_element_id` (FK → `doc_elements`, **`ON DELETE RESTRICT`**), `ordinal`.

> **Why a join table and not `uuid[]`** (resolving C8). Postgres supports array columns, but an array carries no referential integrity — and the entire contract is *"if the model cannot point to a real element, the field is rejected."* A join table with a real foreign key makes that contract enforced **by the database**, not by application code that might one day be bypassed. `ON DELETE RESTRICT` means an element can never be deleted out from under a live citation.

## 7.3 Openings (FR-2)

**`openings`** — one row per door location. Every field below has a corresponding `field_provenance` row; **no value reaches an estimator without one.**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `project_id`, `extraction_run_id` | uuid FK | |
| `door_number` | text | the key everything hangs off |
| `size_raw` | text | as written, e.g. `3070` |
| `width_inches`, `height_inches` | integer, nullable | parsed by the fixed 4-digit rule (§5.7) |
| `handing` | enum `LH`/`RH`/`LHR`/`RHR`, nullable | |
| `finish_raw` | text, nullable | as written |
| `finish_code_id` | uuid FK → finish_codes, nullable | normalised across both nomenclatures |
| `fire_rating_raw` | text, nullable | as written |
| `fire_rating_minutes` | integer, nullable | 20 / 45 / 60 / 90 |
| `fire_rating_absent` | boolean | **explicit** — "no rating found" must be distinguishable from "not yet extracted" (FR-8) |
| `fire_rating_source_location` | enum `DOOR_SCHEDULE`/`FRAME_SCHEDULE`/`MARGIN_NOTE`/`SPEC`/`UNKNOWN` | **accumulates the empirical answer to Open Item 9** (§5.8) |
| `handing_absent` | boolean | same reasoning as the rating |
| `hardware_group` | text, nullable | `HW-3`, or a direct manufacturer part/series callout |
| `alternate_designation` | text, nullable | free text as written |
| `bid_alternate_id` | uuid FK → bid_alternates, nullable | flag-gated (§7.6) |
| `wall_type` | text, nullable | drives throat depth |
| `throat_depth_id` | uuid FK → throat_depths, nullable | |
| `review_state` | enum | |

## 7.4 Matching

**`matches`** — `id`, `opening_id`, `catalog_item_id`, `rank`, `match_confidence`, **`rating_ok` / `handing_ok` / `finish_ok`** (stored per-constraint so a rejection explains itself), `is_direct_equal`, `substitution_note`, `status` enum `PROPOSED`/`ACCEPTED`/`REJECTED`/`MANUAL`/`AWAITING_RFQ`.

## 7.5 Reference library and pricing

**`catalog_items`** — the central library, independent of any job file (FR-3).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `vendor`, `series`, `part_number` | text | |
| `product_type_band` | enum `COMMODITY`/`RESTROOM_PARTITIONS`/`SPECIALTY`/`CUSTOM_FABRICATED` | drives the margin band |
| `csi_division` | text | 08 / 09 / 10 |
| `finish_code_id` | uuid FK, nullable | |
| `fire_rating_minutes` | integer, nullable | rated hardware is a distinct certified product line |
| `handing` | text, nullable | handed parts are separate SKUs |
| `is_stock` | boolean | the top-10 (extendable to ~20) list |
| `p21_item_id` | text, nullable | **nullable on purpose** — Risk R3 |

**`margin_bands`** — Commodity 27%/0.73, Restroom partitions 35%/0.65, Specialty 40%/0.60, Custom-fabricated 25%/0.75. **Data with an effective date, not constants in code.**

**`vendor_multipliers`** — vendor, tier, multiplier, `effective_date`, `source_sheet_version`. NFR-3 requires traceability to the sheet version *and* tier, so the version is part of the record.

**`finish_codes`** — US ↔ BHMA ↔ description (NR-3). Seeded from §1.3. **US19 and US26D must never collapse to the same row.**

**`throat_depths`** — the five standard sizes plus an `is_custom` flag. A table, not a pick-list.

**`tax_rates`** — jurisdiction, rate, `effective_date`. OH and KY only. *(added — §1.1 engineering note)*

**`quote_lines`**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `project_id`; `opening_id`, `match_id`, `catalog_item_id` (all nullable) | uuid FK | nullable allows free-form lines and the accessories block |
| `line_group` | enum `DOOR`/`RESTROOM_ACCESSORIES`/`FREIGHT`/`OTHER` | FR-7 grouping |
| `quantity` | numeric | **human-entered** |
| `our_cost` | numeric | **human-entered or sourced** |
| `cost_source` | enum `P21_LAST_PO`/`DISTRIBUTOR_SHEET`/`MFR_LIST`/`VENDOR_RFQ`/`MANUAL` | the waterfall in priority order |
| `cost_effective_date` | date, nullable | |
| `cost_is_stale` | boolean | computed against a **configurable** freshness window |
| `list_price`, `multiplier`, `vendor_multiplier_id` | numeric / uuid FK, nullable | for the list × multiplier path |
| `adders` | jsonb | electrification, NRP hinges, premium/lead-time finishes (NR-4) |
| `margin_pct` | numeric | **human-editable** |
| `margin_band_id` | uuid FK | the default it came from |
| `margin_overridden` | boolean | |
| `margin_override_reason` | text, nullable | e.g. sourcing-driven, the Wendy's case |
| `below_floor_flag` | boolean | FR-15 — the flag, not the workflow |
| `sale_each`, `extended`, `subtotal` | numeric | **stored, not computed on read** |

`sale_each = our_cost / (1 - margin_pct)`. Only Quantity, Our Cost, and Margin are human-entered. **No `unit_weight` column** — that legacy field is confirmed obsolete and is not rebuilt.

**`vendor_rfqs`** (FR-16) — quote line, vendor, `requested_at`, `returned_price`, `returned_at`, `price_may_be_stale`.

**`feedback`** (FR-13) — entity type + id, field name, `value_before`, `value_after`, `changed_by`, `extraction_run_id`, `changed_at`. Written on every review-UI edit. This is the tuning dataset.

## 7.6 Flag-gated (FR-14)

**`bid_alternates`** — project, `designation`, `description`, `source_document_id`, `is_base_bid`. Built so base-bid and alternate totals present as separate comparable figures. **No reconciliation logic and no UI until Open Item 11 is answered.**

**`page_diffs`** *(added — §4.7)* — `document_id`, `compared_to_document_id`, `page_number`, `status` enum `UNCHANGED`/`CHANGED`/`ADDED`/`REMOVED`. The diff report, which is safe to build now; reconciliation is not.

## 7.7 Job tracking

**`pipeline_jobs`** — project, document, `stage` enum (`PREPROCESS` → `OCR` → `NORMALIZE` → `EXTRACT` → `LINK` → `MATCH` → `PRICE`), `status`, `attempt`, `idempotency_key`, `external_job_id` (the Textract job ID, **written before the call returns** — §9 B8), `error_detail`, `cost_estimate`, `cost_actual`, timestamps. The shared handoff record between Django and the FastAPI worker; the status poll reads it.

---

# 8. Project structure

Monorepo. Two Python services, one Next.js app, one Terraform tree, one ops tree. The layout below fixes C10 (`apps/` vs `app/`) and gives every new concept in §4–§6 an unambiguous home.

## 8.1 Repository layout

```
cbc-copilot/
├── README.md
├── Makefile                          # single entry point for every local task
├── docker-compose.yml                # api, pipeline, frontend, postgres, ministack
├── .env.example                      # every variable, no secrets
│
├── backend/
│   ├── pyproject.toml                # uv-managed; one lockfile for both services
│   ├── uv.lock
│   ├── Dockerfile.api
│   ├── Dockerfile.pipeline
│   │
│   ├── api/                          # ── Django 5 + DRF  (was: backend/apps/) ──
│   │   ├── manage.py
│   │   ├── config/
│   │   │   ├── settings/  base.py · local.py · staging.py · production.py
│   │   │   ├── urls.py · asgi.py · wsgi.py
│   │   ├── authentication/           # Django auth (Cognito deferred — C3)
│   │   ├── projects/                 # projects, documents, upload, storage_ops
│   │   │   ├── models.py · upload.py · storage_ops.py · signals.py
│   │   │   └── migrations/           # ★ ALL migrations live here. Single source.
│   │   ├── openings/                 # openings, field_provenance read APIs
│   │   ├── catalog/                  # catalog_items, finish_codes, throat_depths
│   │   ├── pricing/                  # margin_bands, vendor_multipliers, tax_rates
│   │   ├── quotes/                   # quote_lines, approval, export trigger
│   │   ├── feedback/                 # FR-13 capture
│   │   └── common/                   # shared serializers, permissions, pagination
│   │
│   ├── pipeline/                     # ── FastAPI worker  (was: backend/app/) ──
│   │   ├── main.py                   # app + lifespan SQS consumer
│   │   ├── settings.py
│   │   ├── consumers/
│   │   │   ├── document_ready.py     # SQS document-ready
│   │   │   └── ocr_complete.py       # SNS→SQS Textract completion (§9 B2)
│   │   ├── stages/
│   │   │   ├── preprocess.py         # §4 — validate, probe, classify, manifest
│   │   │   ├── raster.py             # §4.5 — pre-render pages to S3
│   │   │   ├── ocr.py                # §4.4 — Textract routing (TABLES/TEXT/native)
│   │   │   ├── normalize.py          # → doc_elements, bulk COPY (§9 B3)
│   │   │   ├── extract.py            # §5 — Bedrock, two-pass
│   │   │   ├── link.py               # §5.6 — citation + grounding validation
│   │   │   ├── match.py              # §6.1 — deterministic
│   │   │   └── price.py              # §6.2 — deterministic
│   │   ├── llm/
│   │   │   ├── bedrock.py            # client, model-ID resolution, retry, caching
│   │   │   ├── prompts/
│   │   │   │   ├── locate/v1.md
│   │   │   │   └── extraction/v1.md v2.md v3.md    # versioned, never edited in place
│   │   │   ├── schemas/              # JSON Schema / tool definitions
│   │   │   └── validators/           # citation existence, value grounding
│   │   ├── parsers/                  # §5.7 deterministic post-parsers
│   │   │   ├── size.py · finish.py · fire_rating.py · handing.py
│   │   ├── db/
│   │   │   ├── tables.py             # SQLAlchemy Core — mirrors Django schema
│   │   │   └── bulk.py               # COPY helpers
│   │   ├── observability/            # structured logging, metrics, cost accounting
│   │   └── tests/
│   │
│   ├── shared/                       # imported by BOTH services
│   │   ├── enums.py                  # single definition of every enum
│   │   ├── s3_keys.py                # single definition of every key template
│   │   └── config.py
│   │
│   └── tests/
│       ├── integration/
│       │   └── test_schema_parity.py # ★ asserts SQLAlchemy tables == live schema
│       └── golden/
│           ├── manifest.yaml         # golden-set index (PDFs stored in S3, not git)
│           ├── labels/               # field-level ground truth per bid set
│           └── test_extraction_eval.py
│
├── frontend/                         # ── Next.js 16 (App Router) ──
│   ├── package.json · next.config.ts · tsconfig.json
│   ├── Dockerfile
│   └── src/
│       ├── app/
│       │   ├── (auth)/login/
│       │   ├── projects/[id]/
│       │   │   ├── page.tsx
│       │   │   ├── openings/         # the grid with confidence badges
│       │   │   ├── source/           # page raster + polygon overlay viewer
│       │   │   └── quote/            # review, edit, approve, export
│       │   └── catalog/
│       ├── components/
│       │   ├── openings-grid/
│       │   ├── source-viewer/        # ★ overlay is CLIENT-side (§9 B5)
│       │   ├── confidence-badge/
│       │   └── quote-editor/
│       ├── lib/
│       │   ├── api.ts                # ★ generated from OpenAPI — never hand-typed
│       │   ├── types.generated.ts    # ★ from drf-spectacular schema (fixes H2)
│       │   └── query/
│       └── styles/
│
├── infra/                            # ── Terraform ──
│   ├── modules/
│   │   ├── network/   vpc, subnets, SGs, S3 gateway endpoint (free)
│   │   ├── storage/   source bucket (Object Lock GOVERNANCE), derived, lifecycle
│   │   ├── database/  RDS Postgres, parameter group, subnet group
│   │   ├── queue/     SQS + DLQ + redrive, SNS topic for Textract
│   │   ├── compute/   EC2 api host, EC2 worker host, IAM roles
│   │   ├── cdn/       CloudFront over derived bucket
│   │   ├── ai/        Bedrock model access, Textract IAM policy
│   │   └── observability/  log groups w/ retention, alarms, budgets
│   ├── envs/
│   │   ├── dev/       main.tf · terraform.tfvars · backend.tf
│   │   ├── staging/
│   │   └── prod/
│   └── README.md                     # incl. the manual Bedrock model-access grant
│
├── ops/
│   ├── scripts/      seed_reference_data.py · calibrate_threshold.py · cost_report.py
│   ├── runbooks/     dlq-drain.md · rotate-secrets.md · restore-from-snapshot.md
│   └── ci/           github-actions workflows
│
└── docs/
    ├── CBC_Copilot_Consolidated_Spec.md      # this document
    ├── adr/                                  # architecture decision records
    │   ├── 0001-django-owns-schema.md
    │   ├── 0002-join-table-not-array.md
    │   ├── 0003-page-triage-before-ocr.md
    │   └── 0004-cognito-deferred.md
    └── archive/                              # the five superseded documents
```

## 8.2 Structural rules

| Rule | Why |
|---|---|
| **All migrations are Django migrations, in `api/projects/migrations/`** | One schema owner. The FastAPI service uses SQLAlchemy Core against Django-migrated tables — no second migration tool, no `Base.metadata.create_all` |
| **`test_schema_parity.py` runs in CI** | Asserts the SQLAlchemy table definitions match the live schema. Drift fails the build rather than production |
| **`shared/enums.py` and `shared/s3_keys.py` are the only definitions** | Two services duplicating an enum is how `READY_FOR_PROCESSING` becomes `READY` in one of them |
| **Frontend types are generated from the OpenAPI schema** | Directly fixes H2. A hand-maintained `types.ts` drifting from the API is the defect that broke the current frontend |
| **Prompts are versioned files, never edited in place** | `extraction_runs.prompt_version` must resolve to an exact artefact |
| **Golden-set PDFs live in S3, not git** | Client bid sets are confidential and large. Git holds the manifest and the labels |
| **`Dockerfile.api` and `Dockerfile.pipeline` are separate** | The pipeline needs PyMuPDF and image libraries; the API does not. Separate images keep the API small and its attack surface narrow |
| **Both images build for `linux/arm64`** | Required for the Graviton hosts in §3.1 |

## 8.3 Environments

| Env | Compute | Data | Purpose |
|---|---|---|---|
| **local** | Docker Compose — api, pipeline, frontend, postgres, **MiniStack** (S3 + SQS emulation) | Seeded fixtures | Full offline loop. Textract and Bedrock are the only services stubbed; a `FAKE_OCR=1` mode replays a recorded OCR JSON so the whole pipeline runs with no AWS calls and no spend |
| **dev** | Shared AWS account, **scheduled 07:00–20:00 weekdays** | `db.t4g.micro`, ephemeral | Integration. Off-hours shutdown saves ~65% (§10.3) |
| **staging** | Mirrors prod topology at smaller size | Anonymised copy of prod reference data; **real bid sets only with CBC consent** | Pre-release verification, threshold calibration, golden-set runs |
| **prod** | §3.1 topology | RDS with 7-day PITR + daily snapshots | |

## 8.4 Configuration and secrets

**Precedence:** process env → SSM Parameter Store (prod/staging) → `.env` (local only) → defaults in `settings.py`. Nothing reads a secret from a file in prod. The application fails to start on a missing required variable — never silently defaults.

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://…` | SSM SecureString in prod; via PgBouncer |
| `AWS_REGION` | `us-east-1` | must offer Textract async **and** Bedrock model access |
| `S3_SOURCE_BUCKET` / `S3_DERIVED_BUCKET` | | source has Object Lock (Governance) |
| `CLOUDFRONT_DOMAIN` | | serves page rasters |
| `DOCUMENT_READY_QUEUE` | `document-ready` | already provisioned |
| `DOCUMENT_READY_DLQ` | `document-ready-dlq` | **new** (C6) |
| `TEXTRACT_SNS_TOPIC_ARN` / `TEXTRACT_SNS_ROLE_ARN` | | completion notification (§9 B2) |
| `BEDROCK_MODEL_ID` | *resolved at deploy* | **never hardcoded** (C5); pinned in SSM |
| `BEDROCK_MODEL_ID_CHEAP` | *resolved at deploy* | Haiku tier for classification |
| `EXTRACTION_PROMPT_VERSION` | `v3` | resolves to `llm/prompts/extraction/v3.md` |
| `CONFIDENCE_THRESHOLD_DEFAULT` | `0.80` | **placeholder until calibrated** (§5.9) |
| `CONFIDENCE_THRESHOLD_FIRE_RATING` | `0.95` | per-field, stricter |
| `COST_FRESHNESS_MONTHS` | `8` | configurable, not hardcoded |
| `OCR_ROUTE_CONFIG` | path/JSON | the §4.4 routing table as config (Risk R1) |
| `MAX_OCR_COST_PER_DOCUMENT_USD` | `2.00` | hard budget guard; exceeding it pauses and asks |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` | |

## 8.5 Make targets

```
make up            # docker compose up --build; all five services healthy
make migrate       # Django migrations
make seed          # reference data: finish_codes, throat_depths, margin_bands, tax_rates
make test          # backend unit + integration (incl. schema parity)
make eval          # golden-set extraction evaluation; prints per-field metrics
make calibrate     # threshold curve from the golden set (§5.9)
make cost-report   # per-bid-set AWS cost attribution from CloudWatch + job records
make lint          # ruff + mypy + eslint + tsc
make types         # regenerate frontend types from the OpenAPI schema
```

---

# 9. Bottlenecks — identified and resolved

Seventeen bottlenecks were identified by walking the corrected architecture against the real workload: 40–200 page architectural PDFs, tens of thousands of OCR elements per bid set, and a review UI whose central feature is rendering PDF regions on demand. Each is stated with its symptom, its cause, and the fix. **Ranked by impact.**

## 9.1 Critical — fix before Phase 1 exits

### B1 — OCR runs on every page of a plan set
**Symptom:** minutes of latency per bid set; the dominant AWS line item; NFR-6 unreachable (C17).
**Cause:** no preprocessing stage existed. Every source document went from upload straight to `AnalyzeDocument` on the whole file.
**Fix:** §4 page triage. Classify pages, route only schedules to `TABLES`, prose to `DetectDocumentText` or the native text layer, drawings to nothing.
**Effect:** ~23× cost reduction on the largest line; OCR wall time from minutes to seconds. **The single highest-value change in this document.**

### B2 — Polling `GetDocumentAnalysis` occupies the worker
**Symptom:** a worker process blocked in a sleep-poll loop for minutes per document; API throttling under concurrent jobs; no worker capacity for other bid sets.
**Cause:** the source documents specify *"Poll GetDocumentAnalysis, accumulating Blocks across NextToken pages."*
**Fix:** pass `NotificationChannel` (SNS topic + role ARN) to `StartDocumentAnalysis`. Textract publishes completion to SNS → SQS → a second consumer (`consumers/ocr_complete.py`) that fetches results. The worker submits and moves on.
**Effect:** worker concurrency limited by real work, not by waiting. Removes an entire class of throttling failure.

### B3 — Row-by-row inserts into `doc_elements`
**Symptom:** normalisation takes minutes; RDS CPU credits drain during ingest; the write is the slowest stage in the pipeline.
**Cause:** tens of thousands of rows per bid set inserted through an ORM one at a time.
**Fix:** `COPY` via `psycopg` binary copy, batched 5,000–10,000 rows, one transaction per document. Never the ORM for this table.
**Effect:** typically 50–100× faster than per-row inserts, and it removes the largest single load spike on a burstable database instance.

### B5 — Server-side PDF cropping on every "show source" click
**Symptom:** a 1–3 second CPU and memory spike per click, on the same host serving every estimator's page loads. This is the feature estimators will click most.
**Cause:** the design renders the PDF page region at request time with PyMuPDF. It is also the reason the API host was sized at 8 GiB.
**Fix:** invert it. Pre-render each page **once** at ingest to the derived bucket (§4.5); serve via CloudFront; **overlay the polygon client-side** as an absolutely-positioned SVG over the image. The polygon is already in 0–1 page fractions, which map directly to CSS percentages — no server-side geometry at all.
**Effect:** a CPU-bound Python call becomes a CDN GET. Removes the memory spike that drove the sizing, removes per-click latency, and makes the viewer work identically for ten users or a hundred.

### B8 — Non-idempotent OCR job submission
**Symptom:** duplicate Textract jobs on retry — real money, silently spent. A 3,000-page document re-submitted three times costs $135 instead of $45.
**Cause:** SQS at-least-once delivery plus a 15-minute visibility timeout means a slow job **will** be redelivered. Nothing in the design prevents a second `StartDocumentAnalysis`.
**Fix:** compute `idempotency_key = sha256(document_version_id + feature_set + route_config_version)`. **Write `pipeline_jobs.external_job_id` before the call is considered complete.** On redelivery, if a job ID exists for the key, resume from it rather than re-submitting. Same pattern for Bedrock calls keyed on `(document_id, table_id, prompt_version)`.
**Effect:** a retry storm cannot double-bill. This is a correctness fix as much as a cost fix.

## 9.2 High — fix during Phase 1–2

### B4 — JSONB polygons
**Symptom:** `doc_elements` is far larger than necessary; every read unpacks JSON; no useful index on geometry.
**Cause:** `polygon jsonb` storing `[[x,y],[x,y],[x,y],[x,y]]` — roughly 100+ bytes of structure for 32 bytes of data, per element, at tens of thousands of elements per bid set.
**Fix:** eight `real` columns plus four derived bbox columns (§7.2). Index the bbox.
**Effect:** materially smaller table, no deserialisation on read, and spatial filtering becomes an index scan.

### B6 — Whole-document context to the model
**Symptom:** large per-call cost, latency, and quality degradation on long contexts; one bad page fails the whole document.
**Cause:** the natural but wrong reading of "send the normalised elements to Claude."
**Fix:** §5.3 two-pass, table-scoped batching, plus prompt caching on a stable prefix (§5.12).
**Effect:** bounded and predictable cost per opening; isolated failures.

### B7 — Single queue, no DLQ
**Symptom:** one malformed document crash-loops the worker forever, blocking every other bid set. This has already happened once in this repository — defect H1 is a crash-loop.
**Cause:** no redrive policy specified anywhere in the source documents (C6).
**Fix:** DLQ with `maxReceiveCount: 3`; a `QUARANTINED` state on `pipeline_jobs`; a CloudWatch alarm on DLQ depth > 0; a documented drain runbook (`ops/runbooks/dlq-drain.md`).
**Effect:** a poison pill quarantines itself and pages someone, instead of consuming the pipeline.

### B10 — Connection pool exhaustion
**Symptom:** intermittent `too many connections`, worst during a deploy when old and new processes overlap.
**Cause:** Django (Gunicorn × 3 workers), the FastAPI worker's async pool, Celery-style jobs, migrations, and admin sessions all opening connections to one `db.t4g.medium`.
**Fix:** PgBouncer in transaction mode (or RDS Proxy). **Explicit pool caps on every client** — never framework defaults. Django `CONN_MAX_AGE` tuned to the pooler, not to the database.
**Effect:** connection count decoupled from process count, and deploys stop being a failure window.

### B11 — N+1 lookups in pricing
**Symptom:** pricing a 40-line quote issues hundreds of queries; visible lag on a screen estimators use constantly.
**Cause:** per-line catalogue, multiplier, and margin-band lookups.
**Fix:** batch-resolve all costs for a quote in one query set. Cache `vendor_multipliers` and `margin_bands` in-process keyed by `(vendor, tier, effective_date)`; invalidate on steward update. These tables are small and change rarely — they belong in memory.

### B12 — Provenance join fan-out on the openings grid
**Symptom:** the grid — the primary screen — joins `field_provenance` → `field_provenance_elements` → `doc_elements` for every field of every opening.
**Cause:** normalised storage read as if it were a view model.
**Fix:** denormalise `page_number` and the union `bbox` onto `field_provenance` (§7.2). The grid reads one table; only the detail view traverses the join.

## 9.3 Medium — fix before go-live

### B9 — Burst CPU-credit exhaustion
**Fix:** alarm on `CPUCreditBalance` for both EC2 instances and RDS before go-live; enable T4g Unlimited on the *worker* deliberately with a billing alarm. See §3.4.

### B13 — Addendum reprocessing re-OCRs unchanged pages
**Fix:** §4.7 page hashing and diff. Unchanged pages reuse existing elements and extractions at zero cost.

### B14 — Synchronous quote PDF rendering
**Symptom:** WeasyPrint on the request thread blocks a worker for seconds on a large quote.
**Fix:** enqueue the render; return a job ID; notify on completion. Same pattern as every other long operation.

### B15 — Reference library read on every match
**Fix:** in-process read-through cache with an effective-date key; explicit invalidation when a steward updates a sheet. Never a time-based TTL on pricing data — a stale multiplier silently applied is exactly Risk R5.

### B16 — CloudWatch log groups default to never expire
**Symptom:** a cost line that grows forever and is never noticed because it starts small.
**Fix:** set retention explicitly on **every** log group in Terraform — 30 days for application logs, 7 for debug, 365 for audit. Use the Infrequent Access log class for verbose pipeline logs.

### B17 — S3 Object Lock in Compliance mode is irreversible
**Symptom:** *(latent, catastrophic)* a misconfigured retention period on the source bucket cannot be shortened or removed **by anyone, including the account root user, for the entire retention duration.** A 10-year retention set by a typo is a 10-year bill and a 10-year data-residency obligation.
**Cause:** the source documents specify "Object Lock" without specifying the mode.
**Fix:** use **Governance mode**, not Compliance. Governance provides identical day-to-day immutability — the intake path's guarantee is preserved exactly — but permits an explicitly privileged role holding `s3:BypassGovernanceRetention` to correct a mistake. Grant that permission to a break-glass role only, and alarm on its use. Adopt Compliance mode only if a CBC regulatory requirement demands it, and only after the retention period is signed off in writing.
**Effect:** removes an unrecoverable failure mode at zero cost to the security posture.

## 9.4 Summary

| # | Bottleneck | Severity | Fix | Section |
|---|---|---|---|---|
| B1 | OCR on every page | **Critical** | Page triage | §4.3–4.4 |
| B2 | Textract polling loop | **Critical** | SNS completion notification | §3.2 |
| B3 | Row-by-row element inserts | **Critical** | Bulk `COPY` | §7.2 |
| B5 | Server-side crop per click | **Critical** | Pre-render + client-side overlay | §4.5 |
| B8 | Non-idempotent OCR submission | **Critical** | Idempotency key + early job-ID write | §7.7 |
| B4 | JSONB polygons | High | Typed columns + bbox index | §7.2 |
| B6 | Whole-document LLM context | High | Two-pass, table-scoped, cached prefix | §5.3, §5.12 |
| B7 | No DLQ | High | DLQ + quarantine + alarm | §3.1 |
| B10 | Connection pool exhaustion | High | PgBouncer + explicit caps | §3.4 |
| B11 | N+1 in pricing | High | Batch resolve + reference cache | §6.2 |
| B12 | Provenance join fan-out | High | Denormalise page + bbox | §7.2 |
| B9 | CPU-credit exhaustion | Medium | Alarms + deliberate Unlimited | §3.4 |
| B13 | Addendum re-OCR | Medium | Page hashing + diff | §4.7 |
| B14 | Synchronous PDF render | Medium | Enqueue | §6.2 |
| B15 | Uncached reference library | Medium | Read-through cache | §6.1 |
| B16 | Unbounded log retention | Medium | Explicit retention in Terraform | §10.3 |
| B17 | Object Lock Compliance mode | Medium (latent, severe) | Governance mode + break-glass role | §11.3 |

---

# 10. Cost model and AWS billing reduction playbook

## 10.1 What was wrong with the previous model

Architecture v2 projected **~$187/month** at 150 bid sets and 3,000 pages. That figure omitted EBS, RDS storage and backups, data transfer, any networking beyond "VPC + security groups," frontend hosting, S3 request charges, and DNS — and it sized a two-component system that does not match the real four-component topology (C4, C12). Corrected below.

## 10.2 Corrected baseline — naive implementation

150 bid sets/month, ~3,000 pages, 10 concurrent users, us-east-1 on-demand list. This is what the system costs **before** any optimisation.

| Component | Spec | Monthly |
|---|---|---|
| EC2 — API + web | t3.large, 2 vCPU / 8 GiB | $60.74 |
| EC2 — pipeline worker | t3.medium, 2 vCPU / 4 GiB | $30.37 |
| EBS — 2 × 100 GB gp3 | | $16.00 |
| RDS PostgreSQL | db.t4g.medium, Single-AZ | $47.45 |
| RDS storage + backups | 100 GB gp3 + snapshots | $13.00 |
| S3 | ~50 GB Standard + requests | $1.60 |
| SQS | low volume | $1.00 |
| SSM Parameter Store | SecureString, low volume | $1.00 |
| CloudFront | low volume | $2.00 |
| Route 53 | hosted zone | $0.50 |
| Data transfer out | modest | $5.00 |
| **Amazon Textract** | **3,000 pages, all `TABLES` @ $15/1k** | **$45.00** |
| Amazon Bedrock | reasoning, mid volume, no optimisation | $23.00 |
| CloudWatch | logs + alarms, default retention | $5.00 |
| **Total** | | **≈ $252/month** |

*(The $252 figure includes the separate worker instance from C4/B1. Architecture v2's $187 assumed one host and omitted the seven line items above.)*

## 10.3 The billing reduction playbook

Ranked by savings per unit of effort. Items 1–4 are the ones that matter; the rest are hygiene that compounds.

### 1. Page triage before OCR — save ~$38/month (85% of the Textract line)

The largest lever by a wide margin, and it is an application change, not an AWS setting.

| | Pages | Rate | Cost |
|---|---|---|---|
| Naive | 3,000 × `TABLES` | $15/1k | **$45.00** |
| Triaged | 400 × `TABLES` | $15/1k | $6.00 |
| | 800 × `DetectDocumentText` | $1.50/1k | $1.20 |
| | 1,800 skipped or native text | $0 | $0.00 |
| **Triaged total** | | | **$7.20** |

`DetectDocumentText` is **10× cheaper than `AnalyzeDocument` with `TABLES`**, and Layout is included at no extra charge whenever Tables is enabled — so there is never a reason to pay for Layout separately. Also worth benchmarking: Textract **Queries** targets named fields at a rate well below Tables. If CBC's door schedules turn out to be structurally consistent enough, Queries may cover the schedule fields more cheaply than full table analysis. Verify the current rate against the AWS pricing page before committing.

**Effort:** medium (it is §4). **Saving: ~$38/month, and it is the same change that makes NFR-6 achievable.**

### 2. Bedrock: model tiering + prompt caching — save ~$13/month (55% of the LLM line)

| Technique | Mechanism | Effect |
|---|---|---|
| **Model tiering** | Haiku for page classification and table location; Opus only for schedule interpretation | The premium model sees a small fraction of the tokens |
| **Prompt caching** | Cache the static prefix — system prompt, finish-code table, few-shot examples. Cached input tokens bill at roughly a tenth of the standard rate; the cache write costs about 1.25×, so a prefix reused twice within the TTL is already ahead | Large reduction on a prefix that is identical across every call |
| **Scoped context** | Table-scoped batches instead of whole documents (§5.3) | Fewer tokens per opening, and the count is predictable |
| **Idempotency** | No duplicate calls on retry (B8) | Removes an invisible cost class |

Prompt caching constraints to design around: the cacheable prefix must be **≥ 1,024 tokens**, there is a small maximum number of cache checkpoints, and the prefix must be **byte-identical** across calls. Put the stable content first and never interpolate a document ID, timestamp, or run ID into it.

> **Batch inference is not available here.** Bedrock Batch offers a 50% discount, but current reporting indicates it does not support tool calling or structured output — which the extraction contract requires (§5.4). Verify before planning around it. Batch **is** appropriate for evaluation runs, bulk re-extraction after a prompt change, and backfills, where structured output can be relaxed or post-processed.

### 3. Graviton + commitment pricing — save ~$43/month (45% of the compute line)

| Move | From | To | Saving |
|---|---|---|---|
| API host to Graviton | t3.large $60.74 | **t4g.large $49.06** | $11.68 |
| Worker to Graviton | t3.medium $30.37 | **t4g.medium $24.53** | $5.84 |
| **Compute Savings Plan**, 1-year no upfront | $73.59 on-demand | ~$53 | ~$21 |
| **RDS Reserved Instance**, 1-year no upfront | $47.45 | ~$31 | ~$16 |

Graviton requires arm64 images (§8.2) — Python, Django, FastAPI, PyMuPDF, and Next.js all build cleanly on arm64. Commitment pricing is close to free money for a workload that runs 24/7 with a known baseline: this is an internal tool for a fixed team, which is the ideal Savings Plan profile. **Buy the plan after two weeks of steady-state measurement**, sized to the observed floor, not the projected peak.

### 4. Networking: no NAT gateway — save ~$33/month, and avoid a future $100+

A NAT Gateway costs about **$0.045/hour (~$33/month) plus $0.045/GB processed**, and internet egress on top of that. For this workload it is entirely avoidable:

- **S3 Gateway VPC Endpoint is free** — no hourly charge, no per-GB charge. Put one in every VPC unconditionally. This alone eliminates NAT data-processing charges on the highest-volume traffic in the system.
- Keep app hosts in a **public subnet with no inbound rules** (SSM Session Manager for access, no SSH, no public ingress except via the load balancer or CloudFront). RDS stays private. This is a common, defensible pattern at this scale and needs no NAT at all.
- If CBC IT later requires fully private egress under NFR-4, **interface endpoints** for Textract, Bedrock, SSM, and CloudWatch cost about **$0.01/hour per AZ plus $0.01/GB** — versus NAT's $0.045/GB. Three or four interface endpoints in one AZ land near a single NAT gateway's hourly cost while eliminating the 4.5× per-GB premium. Model it against measured traffic before choosing.

### 5. Storage lifecycle and compression — save ~$1–3/month now, much more later

- **Gzip the OCR JSON before writing to S3.** Textract output is extremely repetitive and compresses roughly 10–20×. This reduces storage, transfer, and the time spent reading it back.
- **S3 Intelligent-Tiering** on the derived bucket — OCR JSON and page rasters are written once and read rarely. Worth it for objects over 128 KB, which these are. (The small monitoring fee makes it a loss on tiny objects, so do not blanket-apply it.)
- **Lifecycle rules:** derived artefacts → Glacier Instant Retrieval at 90 days. Source PDFs stay retrievable, but their **storage class** can still transition even under Object Lock — the lock governs deletion, not tiering.
- **Abort incomplete multipart uploads after 7 days.** A one-line lifecycle rule that prevents a cost line most teams discover only in an audit.

### 6. CloudWatch retention — save ~$3/month, growing

Log groups default to **never expire**. Set retention explicitly on every group in Terraform. Use the Infrequent Access log class for verbose pipeline logs. Sample debug logs in production rather than emitting them at full volume.

### 7. Non-production scheduling — save ~65% of dev and staging

Dev and staging run 168 hours a week and are used for perhaps 50. Stop EC2 and RDS outside 07:00–20:00 on weekdays (§8.3). RDS instances can be stopped for up to 7 days before auto-restart, so pair the stop schedule with a weekly restart guard. On a dev environment costing ~$60/month this returns ~$40.

### 8. Guardrails — the ones that make the rest stick

- **AWS Budgets** with alerts at 50%, 80%, and 100% of a monthly target — configured in Terraform on day one, not after the first surprise.
- **Cost Anomaly Detection** on Textract and Bedrock specifically. These are the two lines that can move 10× overnight from a code change.
- **Cost allocation tags** — `env`, `service`, `component` — on every resource, enforced by a Terraform module default.
- **`MAX_OCR_COST_PER_DOCUMENT_USD`** (§8.4) — an application-level hard guard. A document whose estimated OCR cost exceeds the limit pauses and asks rather than spending. This is the only control that catches "someone uploaded a 3,000-page set by mistake" *before* the money is gone.
- **Cost per bid set as a first-class metric.** `pipeline_jobs.cost_actual`, reported by `make cost-report`. A per-bid unit cost is what makes a regression visible in week one instead of at month end.

## 10.4 Optimised cost model

| Component | Naive | **Optimised** | Technique |
|---|---|---|---|
| EC2 — API + web | $60.74 | **$35.00** | Graviton + Savings Plan |
| EC2 — worker | $30.37 | **$18.00** | Graviton + Savings Plan |
| EBS | $16.00 | $16.00 | — |
| RDS instance | $47.45 | **$31.00** | Reserved Instance |
| RDS storage + backups | $13.00 | $13.00 | — |
| S3 | $1.60 | **$0.90** | Gzip + Intelligent-Tiering + lifecycle |
| SQS | $1.00 | $1.00 | — |
| SSM | $1.00 | $1.00 | — |
| CloudFront | $2.00 | $2.00 | — |
| Route 53 | $0.50 | $0.50 | — |
| Data transfer | $5.00 | **$3.00** | S3 gateway endpoint |
| NAT Gateway | *(would be $33)* | **$0.00** | Public subnet + gateway endpoint |
| **Textract** | **$45.00** | **$7.20** | **Page triage (§4)** |
| **Bedrock** | **$23.00** | **$10.00** | **Tiering + caching + scoping** |
| CloudWatch | $5.00 | **$2.00** | Retention + IA log class |
| **Total** | **$252** | **≈ $141/month** | **44% reduction** |

Add **~$25/month** for a scheduled dev environment and **~$45/month** for staging when they are running, for an all-in platform cost around **$210/month**.

## 10.5 Cost scenarios

| Scenario | Compute | Database | Textract | Total |
|---|---|---|---|---|
| **Minimum viable** — single host, no worker separation | t4g.large $35 | db.t4g.medium $31 | triaged $7 | **~$110** |
| **Recommended** (this document) | t4g.large + t4g.medium $53 | db.t4g.medium $31 | triaged $7 | **~$141** |
| **Headroom** — RDS throttling observed | $53 | db.m6g.large Multi-AZ ~$115 | $7 | **~$225** |
| **Naive** — no optimisation, no triage | t3 pair $91 | db.t4g.medium $47 | all-Tables $45 | **~$252** |
| **Growth** — 3× volume, 500 bid sets/month | $53 | $31 | $22 | **~$175** |

The growth row is the important one: because the fixed infrastructure dominates and the variable cost was optimised, **tripling the document volume adds roughly $34/month, not $200.** That is the return on the triage work.

> **Verify before committing.** All figures are us-east-1 on-demand list at the time of writing and are directionally reliable, not contractual. AWS pricing changes; regional rates differ; Textract and Bedrock rates in particular have moved more than once. Re-check against the AWS pricing pages and model with the AWS Pricing Calculator before this goes into a budget.

---

# 11. Security, compliance, observability

## 11.1 Data residency (NFR-4)

Customer drawings, pricing, and quotes **never leave the AWS account**. OCR (Textract) and reasoning (Bedrock) run in the same account and region as S3 and RDS. There is no cross-cloud egress and no third-party document processor in the path. Direct Anthropic API access exists only as a **configuration escape hatch**, disabled in production, and must never become a second extraction path.

⚠ The remaining NFR-4 item is CBC IT **naming AWS as the approved environment**. Obtain that sign-off before the first production bid set. Pin the region and maintain a documented data-flow diagram as the artefact IT signs against.

## 11.2 Access control

- **Django auth** is the authorisation and audit boundary (C3). Cognito or an enterprise IdP may later sit in front as an OIDC provider; Django still owns permissions and the audit trail.
- **IAM roles per service.** The API host's role can write to the source bucket and read the derived bucket. The worker's role can read source, write derived, call Textract and Bedrock. Neither can delete from source.
- **No long-lived AWS keys anywhere.** Instance profiles in AWS; short-lived credentials locally.
- **SSM Session Manager** for host access. No SSH, no bastion, no inbound port 22.
- **Break-glass role** for `s3:BypassGovernanceRetention` (B17), assumable only with MFA, with CloudTrail alarming on every use.

## 11.3 Immutability and retention

| Bucket | Configuration |
|---|---|
| **Source** | Versioning on. **Object Lock in GOVERNANCE mode** (not Compliance — B17). Retention period signed off in writing before enabling. Write-once from the verified intake path only; the existing upload guard rejecting `/derived/` as an inbound path stays. |
| **Derived** | Versioning on, no lock. Lifecycle: Intelligent-Tiering → Glacier Instant Retrieval at 90 days. Abort incomplete multipart uploads at 7 days. Freely rebuildable from source — that is what makes it safe to tier aggressively. |

The intake path's existing guarantees — magic-byte verification, checksum matching, S3 version-ID and SSE enforcement, an idempotent completion step, and a test proving a project rename does not rewrite source keys — are **kept unchanged**. They are more rigorous than the original plan required and nothing about them needs revisiting.

## 11.4 Audit trail (NFR-3)

Every quote line resolves backwards to: the `catalog_item` and `vendor_multiplier` version that priced it → the `match` and its per-constraint verdicts → the `opening` → `field_provenance` → cited `doc_elements` → page number and polygon → the immutable source PDF version in S3. Every step is a foreign key. No step is an inference.

`extraction_runs` additionally records the resolved model ID, prompt version, and inference parameters, so any extracted value is attributable to an exact, reproducible configuration.

## 11.5 Observability

| Signal | Implementation |
|---|---|
| Structured logs | JSON to CloudWatch, correlation ID = `pipeline_job_id`, retention set explicitly per group |
| Stage tracing | X-Ray on the pipeline; each stage a segment, so "where did the 4 minutes go" has an answer |
| Business metrics | Openings extracted per bid set · flagged-field rate · citation-rejection rate · grounding-failure rate · match-acceptance rate · estimator-correction rate |
| **Cost metrics** | OCR pages by route · Bedrock tokens by model tier · **cost per bid set** |
| Alarms | DLQ depth > 0 · `CPUCreditBalance` low (both EC2 hosts + RDS) · `ApproximateAgeOfOldestMessage` · citation-rejection rate above baseline · budget thresholds · anomaly detection on Textract and Bedrock |

The two metrics worth watching most closely are **citation-rejection rate** and **estimator-correction rate**. A rise in the first means the model or prompt has drifted. A rise in the second means the system is confidently wrong — which is the failure mode NFR-2 exists to prevent, and the one that erodes adoption fastest.

---

# 12. Delivery plan and verification

**Guiding rule, unchanged:** *the intake path is correct and stays.* Everything downstream is built fresh.

## 12.1 What gets replaced outright

| Thing | Disposition | Why |
|---|---|---|
| Migrations 0003–0007 (reverted pipeline schema) | **Stay deleted.** Squash 0001–0008 into a single initial migration. | That schema is a *different architecture* — sheet-register / reconciliation-oriented, with one `bbox` per extraction candidate rather than element-level per-word polygons. Reviving it would quietly import competing assumptions about what provenance means. The squash also removes the H5 dead shims, which exist only to satisfy those migrations' imports. |
| MySQL 8.4 | **Replaced by Postgres 17.** | Confirmed decision. `ensure_rds_instance.py` and `RDS_ENGINE_VERSION=8.4` both assume MySQL and must change (C2). |
| `docker-compose.yml` `worker` command | **Replaced** by the FastAPI pipeline service. | Both commands it invokes were never written (H1). |
| `frontend/src/lib/types.ts` | **Replaced by generated types.** | Currently describes deleted models (H2). Generation prevents recurrence. |
| NVIDIA NIM configuration | **Decide, then remove or isolate.** | Provisions `llama-3.3-nemotron-super-49b-v1.5`, framed in the README as a test harness, wired to no Python file. ⚠ Q2. **It must not become an accidental second extraction path.** |
| Amazon Cognito (from Architecture v2) | **Removed from near-term scope.** | C3. |

Everything else — auth, projects, documents, upload, storage, permissions, signals, the Next.js shell, drf-spectacular/Scalar docs — is kept and extended.

## 12.2 Phases

### Phase 0 — Foundation
*Unblocks everything; no product value on its own.*

- Squash migrations 0001–0008; delete the H5 shims.
- Migrate MySQL → Postgres 17; update `ensure_rds_instance.py` and `RDS_ENGINE_VERSION`. ⚠ **Blocked on Q4** (drop vs migrate the dev dataset).
- **Re-audit `textract.py`, `worker.py`, `normalize.py` against C1** — establish what actually runs, since the worker has never booted.
- Rename per C9 and C10: `di_*` → `doc_*`/`ocr_*`; `backend/apps/` → `backend/api/`; `backend/app/` → `backend/pipeline/`.
- Stand up the FastAPI skeleton + `ensure_sqs_queue`; add the DLQ; replace the broken worker command (H1).
- Generate frontend types from the OpenAPI schema (H2); fix H3, H4.
- Terraform: S3 gateway endpoint, log retention, budgets, cost tags, Object Lock **Governance** mode.
- **Exit:** `docker compose up` brings all five services healthy with **no crash-looping container**; the existing 17 tests pass on Postgres; `GET /openapi.json` and `/scalar/` render; `npm run build` clean against generated types.

### Phase 1 — Preprocessing and traceability backbone
*The highest-risk work, done first and alone.*

- §4 preprocessing: validate, probe text layer (including `VECTOR_OUTLINED`), classify pages, build `document_manifest`, pre-render rasters, hash pages.
- Textract via SNS completion notification (B2); route per §4.4; persist raw JSON gzipped and immutably before processing.
- Normalisation → `doc_elements` with bulk `COPY`, typed polygon columns, stable `element_path`.
- Frontend page-raster viewer with **client-side polygon overlay** (B5).
- **Exit:** on a real bid set, click any element and see it highlighted on the correct page of the untouched source PDF. **No Claude involved yet.** If this does not work, nothing downstream is trustworthy, and finding out here is cheap.
- **Verify:** `ocr_result.json.gz` lands on the derived bucket and the source PDF is byte-identical afterward. Every `doc_elements` row has a non-null global page number and four vertices. Re-running normalisation reproduces identical element identities (`unique (document_id, element_path)` holds, no orphaned citations). **Manual check against three page types: a clean schedule table, a rotated/skewed sheet, and a dense drawing.** Assert triage skipped the pages it should have and that skips are visible in the UI.

### Phase 2 — Extraction (FR-2, FR-8)
- Two-pass extraction; structured output; mandatory citations.
- **Citation validation and value grounding** (§5.6).
- `openings` + `field_provenance`; composite confidence; `*_absent` flags.
- Openings grid with confidence badges, wired to the Phase 1 viewer.
- Golden set + evaluation harness; threshold calibration.
- **Verify:** every populated field has a `field_provenance` row with at least one cited element. **The two critical negative tests:** (a) a fabricated `element_id` is **rejected and flagged**, not persisted; (b) a valid `element_id` cited for a value not present in it is **also rejected**. An opening with no fire rating produces `fire_rating_absent = true` and a review flag, not a silent null. US19 and US26D resolve to different `finish_codes` rows. Composite confidence never exceeds either input.

### Phase 3 — Reference library and matching (FR-3, FR-4)
- `catalog_items` + reference data: finish codes, throat depths, margin bands, vendor multiplier tiers with effective dates, tax rates.
- Matching engine: rating and handing hard, finish scored, top-N with per-constraint verdicts, manual cut-off.
- ⚠ **Blocked on NR-6.** The engine is built and tested against a seeded sample; it cannot go live without CBC's top-10 stock list.
- **Verify:** a rated opening never matches an unrated item, and an `LH` opening never matches an `RH`-only SKU — **regardless of how high the text similarity scores**. Below-cutoff matches route to the manual path instead of auto-proposing a line.

### Phase 4 — Pricing and quote (FR-5, FR-6, FR-7, FR-15, FR-16)
- Cost waterfall with `MANUAL` first-class; staleness marking.
- List × multiplier + adders; margin-as-divisor with logged overrides.
- Quote assembly grouped by door, accessories block, freight line.
- Vendor-RFQ loop. Margin-floor **flag** only.
- **Verify:** golden-file test against a real CBC worked example — `sale_each = cost / (1 - margin)`, subtotals and grand total match **to the cent**. The waterfall honours priority order. A cost beyond the freshness window sets `cost_is_stale`. An override records a reason and sets `below_floor_flag` when below band.

### Phase 5 — Export, feedback, templated reuse (FR-10, FR-13, FR-11)
- WeasyPrint customer-facing PDF with standard terms and OH/KY-only tax; routed to the captured initiator.
- Feedback capture on every edit (land the write path in Phase 2 if cheap).
- Prior-quote reuse by brand/architect/GC.
- **Verify:** rendered quote diffed against CBC's current format (⚠ Q10). OH 8% / KY 6.5% applies and no other jurisdiction does. Export routes to `initiator_email`, not a group address. Every review-UI edit writes a `feedback` row with before/after values.

**Deferred deliberately:** FR-12 FRP takeoff (constants outstanding), FR-14 alternates/addenda UX (schema and page-diff built, UI flag-gated), margin-approval routing (out of scope by CBC's own answer).

## 12.3 Phase dependencies

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
   ▲            ▲           ▲           ▲           ▲           ▲
   Q4         Q1, Q9      Q9, Q1      NR-6      NR-7/8/9      Q10
 (dataset)  (region,     (fire      (stock     (adders,     (quote
            sample bids)  rating)    list)     margins)     layout)
```

Phases 0–2 are unblocked by CBC data and can start immediately once Q4 and Q1 are answered. **Phases 3–5 cannot go live without the outstanding CBC data**, though the engines can be built and tested against seeded samples. That ordering is deliberate: it puts the highest-risk engineering (traceability) first and the data-dependent work last, so waiting on CBC never blocks the critical path.

---

# 13. Risk register

Ranked by likelihood of producing a **silent, wrong quote**.

**R1 — Fire-rating location is unanswered, and so is the half nobody notices.**
Open Item 9 asks two things: *where* the rating lives on a bid set, **and** *which categories are rating-sensitive for price*. Only the first is usually discussed. The second means **no rating→price logic can be built yet**. *Mitigation:* make the OCR routing table and extraction hints **configuration**, never a hardcoded column index (§4.4); make `fire_rating_absent` explicit and always flag it; record `fire_rating_source_location` so the system **accumulates the empirical answer** across real bid sets; build no pricing rule keyed off rating until CBC answers.

**R2 — `DOCUMENT_ROLE_ADDENDUM` is already a de-facto answer to an open question.**
The existing role models an addendum as a separate uploaded document. Reasonable — but Open Item 11 is explicitly open, and CBC has not said whether an addendum is a new document, a revision to an existing version, or both. Carrying the current shape forward unexamined is exactly the silent-assumption failure mode. *Mitigation:* keep the role, build `bid_alternates` and `page_diffs`, gate all FR-14 UI behind a flag, build **no** reconciliation logic.

**R3 — P21 item IDs diverge from manufacturer part numbers.**
Flagged in three separate places as requiring a manual-cost fallback **from day one, not as a later patch**. Semi-custom items will not match cleanly. *Mitigation:* `p21_item_id` nullable; `MANUAL` a first-class `cost_source`; never auto-accept a cost match on part-number string similarity alone; always surface the matched P21 record so the estimator can reject it.

**R4 — FR-15 and Open Item 14 contradict each other.**
FR-15 and NFR-8 demand below-floor flagging and approval routing. Open Item 14 answers *"no margin deviation today; approval routing deferred"* and marks FR-15 out of scope. Building the routing implements something CBC deferred; ignoring FR-15 drops something live. *Mitigation:* build the flag with configurable floors, build no workflow, and **surface the tension for a decision** rather than resolving it silently.

**R5 — No named data steward means stale prices will quietly drive real quotes.**
NFR-10 / Open Item 15 remains open: no owner, no refresh cadence for the reference library, multiplier sheets, or margin sheet. The 6–8 month freshness rule is the only guardrail. *Mitigation:* `effective_date` on every price source; `cost_is_stale` against a configurable window; NR-2's refresh prompt in the UI; **no automatic silent refresh**.

**R6 — NFR-4 has no confirmed owner or approved environment.** *(narrowed)*
Cross-cloud drawing egress is closed — Textract and Bedrock run in the same AWS account as S3 and RDS. The remaining item is CBC IT **naming AWS as the approved environment**. *Mitigation:* obtain sign-off before production bid sets; pin the region; maintain the data-flow diagram.

**R7 — The 0.80 confidence threshold is a suggestion, not a validated tolerance.** *(C14)*
*Mitigation:* §5.9 calibration procedure. Measure the flagged-rate/escape-rate curve on the golden set and have **CBC choose the operating point** in terms they own. Per-field thresholds, stricter for rating and handing.

**R8 — Reviving migrations 0003–0007 would import a competing architecture.**
That schema is sheet-register/reconciliation-oriented with one `bbox` per candidate; this design is element-level with per-word polygons. Not compatible provenance models. *Mitigation:* squash and build fresh; do not restore selectively later without revisiting this document.

**R9 — `doc_elements` volume.** *(reduced by triage)*
Tens of thousands of rows per bid set — materially fewer with §4 triage, since skipped pages produce no elements. *Mitigation:* typed polygon columns rather than JSONB (B4); bulk `COPY` (B3); index `(document_id, page_number)`; measure before partitioning. Do not pre-optimise.

**R10 — FRP constants are partial.**
Vu360 is confirmed, but panel size, waste %, trim/stick lengths, and adhesive coverage are outstanding. *Mitigation:* FR-12 stays deferred. **Building the converter on guessed constants produces plausible wrong quantities, which is worse than no feature.**

**R11 — Vector-outlined text is misread as a scan.** *(new — §4.2)*
Architectural PDFs frequently export text as vector outlines. A naive text-layer probe returns nothing, the page is classified as scanned, and OCR of a downsampled render loses the small annotation text where door numbers and ratings live — producing an **empty extraction with high OCR confidence**, the worst failure mode under NFR-2. *Mitigation:* explicit `VECTOR_OUTLINED` detection (`native_word_count < 20 and vector_path_count > 500`); 300 DPI rasterisation before OCR for those pages; a golden-set case that is vector-outlined.

**R12 — Page triage silently skips a page that mattered.** *(new — §4.3)*
Triage is the largest cost and latency win and it introduces a new failure mode: a schedule the classifier did not recognise. *Mitigation:* `SKIP` decisions are **always visible in the UI with a reason**; the estimator can force-read any page; forced reads write `feedback` rows that improve the anchors; classifier recall is measured on the golden set as a first-class metric, and recall is weighted far above precision — a false positive costs $0.015, a false negative costs a missing opening.

---

# 14. Open items and decisions required

## 14.1 Blocking — needed before or during the phase shown

| ID | Item | Owner | Blocks |
|---|---|---|---|
| **Q4** | MySQL → Postgres: can the existing dev dataset (users, projects, documents) be **dropped and recreated**, or must it migrate in place? Dropping is far cheaper, but the S3 objects those `Document` rows point at would be orphaned unless the bucket is cleared too. Also confirm the RDS instance can be reprovisioned as Postgres. | Dash | **Phase 0** |
| **Q1** | Confirm AWS region (must offer Textract async **and** Bedrock model access — `us-east-1` / `us-west-2` are the safe pair), IAM on the app roles, and the **manual Bedrock model-access grant**. | Dash / CBC IT | **Phase 1** |
| **Q9** | **Sample bid sets.** No PDFs exist in the repository. Triage tuning, extraction prompt development, and the golden set all need real door and hardware schedules — **including at least one messy set** (merged cells, rating in margin notes, an addendum, a rotated sheet, a vector-outlined sheet). | CBC | **Phase 1–2** |
| **Open Item 9** | **Fire rating** — where it lives on CBC bid sets, and **which categories are rating-sensitive for price**. Both halves. | CBC Sr. Estimator | **Phase 2–4** |
| **NR-6** | **Top-10 stock list per product type** — foundation for the item picker and custom/other tab. | CBC | **Phase 3 go-live** |
| **NR-7/8/9** | Hager adder values; light-kit table logic; special-customer margins (e.g. Wendy's). | CBC | **Phase 4 go-live** |
| **Q10** | The existing customer-facing **quote PDF layout** and the Excel quote workbook. FR-10 requires matching the current format, and the pricing formulas are described as "porting from Excel to Python" — but the workbook is not in the repository. Also settles C7 (WeasyPrint vs ReportLab). | CBC | **Phase 5** |

## 14.2 Decisions required from Dash

| ID | Decision |
|---|---|
| **Q2** | **What is NVIDIA NIM for?** `.env` provisions `llama-3.3-nemotron-super-49b-v1.5`, the README frames it as a Hercules API test harness, and it is wired to no Python file. Keep it isolated as a test tool, or remove it? **It must not become an accidental second extraction path.** |
| **Q11** | **P21 access mechanism** — read-only API, scheduled export, or manual? NFR-5 says read-only with no write-back, but not how the read happens. NR-10 lists this as "investigate" with no conclusion. |
| **Q12** | Timeline and resourcing — how many engineers, and is there a target date? The phase sequencing is dependency-ordered, not calendar-fitted. |
| **Q13** | Who owns the FastAPI worker's deployment and on-call? The hybrid topology adds a second deploy unit. |
| **C7** | Quote renderer: WeasyPrint (recommended) vs ReportLab. Decidable once Q10 lands. |
| **C15** | Confirm Next.js version against `package.json` at Phase 0. |

## 14.3 Requiring a CBC decision, not just data

| ID | Decision |
|---|---|
| **Open Item 11 / FR-14** | **Alternates and addenda** — how alternates are quoted and how addenda are received and reconciled. Is an addendum a new document, a revision to an existing bid version, or both? Schema and page-diff are built; reconciliation and UI are not. |
| **NFR-4** | CBC IT to **name AWS as the approved environment** and confirm the data-security owner. |
| **NFR-10 / Open Item 15** | **Data stewardship** — named owner and refresh cadence for the reference library, each vendor multiplier sheet, and the margin sheet. Without this, R5 stays live indefinitely. |
| **FR-15 vs Open Item 14** | Resolve the contradiction (C13/R4): FR-15 requires below-floor flagging and approval routing; Open Item 14 defers approval routing. Confirm "flag only, no workflow" is the intended reading. |
| **Open Item 16 / NFR-6** | **Baseline and target metrics** — bids/month, hours/bid, turnaround, hit rate. Also the formal performance target NFR-6 currently lacks. Session hint: automating stock + top-10 vendors could speed ~80–90% of quotes, but this is directional, not contractual. |
| **§5.9** | **Choose the confidence operating point** once calibration produces the curve — expressed as "flag X% of fields, let through roughly 1 error per Y." |
| **Open Item 5** | FRP conversion constants — panel size, waste %, trim/stick lengths, adhesive coverage. Blocks FR-12. |

---

# Appendix A — Decision log

| # | Decision | Rationale | Source |
|---|---|---|---|
| D1 | Hybrid Django + FastAPI; Django owns all migrations | Keeps the working intake path; one schema owner; CI-enforced parity | Dev Plan, retained |
| D2 | Postgres 17 replaces MySQL 8.4 | JSONB, native uuid, GIN indexing; and the whole provenance model assumes it | Dev Plan, retained |
| D3 | Migrations 0003–0007 stay deleted; squash 0001–0008 | Reviving them imports a competing provenance architecture (R8) | Dev Plan, retained |
| D4 | SQS, not HTTP, for the Django→worker handoff | No synchronous cross-service dependency; a worker restart loses nothing | Dev Plan, retained |
| D5 | Join table, not `uuid[]`, for citations | Referential integrity enforces the traceability contract in the database | Dev Plan over Pipeline Plan — **C8** |
| **D6** | **Page triage before OCR** | 23× cost reduction on the dominant line; makes NFR-6 achievable | **New — C17, §4** |
| **D7** | **Value grounding in addition to citation existence** | A valid citation for a fabricated value is worse than no citation | **New — §5.6** |
| **D8** | **Pre-render rasters; overlay client-side** | Removes the per-click CPU spike and the memory profile that drove sizing | **New — B5** |
| **D9** | **Worker on a separate instance** | One large bid set must not degrade every estimator's page loads | **New — C4, §3.4** |
| **D10** | **Cognito deferred; Django auth is the boundary** | Solves a problem this build does not have; adds risk to the riskiest phase | **New — C3** |
| **D11** | **S3 Object Lock in Governance, not Compliance, mode** | Identical day-to-day immutability; removes an unrecoverable failure mode | **New — B17** |
| **D12** | **Model ID resolved at deploy, never hardcoded** | `anthropic.claude-opus-5` is not resolvable; and runs must be attributable to an exact version | **New — C5** |
| **D13** | **No NAT gateway; free S3 gateway endpoint** | Saves ~$33/month and eliminates the highest-volume per-GB charge | **New — §10.3** |
| **D14** | **Graviton (arm64) + Savings Plan + RDS RI** | ~45% off compute for a 24/7 workload with a known baseline | **New — §10.3** |
| **D15** | **`di_*` renamed to `doc_*` / `ocr_*`** | Vestigial Azure names apologised for in three documents; free to fix now | **New — C9** |

---

# Appendix B — Consolidated checklist

**Before writing code**
- [ ] Q4 answered (dataset disposition) · Q1 answered (region, IAM, Bedrock grant)
- [ ] AWS Budgets, Cost Anomaly Detection, and cost tags in Terraform
- [ ] Log-group retention set on every group
- [ ] Object Lock mode confirmed as Governance, retention period signed off in writing

**Before the first real bid set**
- [ ] Q9 satisfied — sample bid sets including a messy one
- [ ] Phase 1 exit verified on a rotated sheet and a vector-outlined sheet
- [ ] `CPUCreditBalance` and DLQ-depth alarms live
- [ ] `MAX_OCR_COST_PER_DOCUMENT_USD` guard active
- [ ] NFR-4 sign-off from CBC IT

**Before production**
- [ ] Confidence thresholds calibrated, not defaulted; CBC has chosen the operating point
- [ ] Golden-set metrics baselined; CI gates on regression
- [ ] Both negative tests passing (fabricated ID; ungrounded value)
- [ ] Pricing golden-file test matching CBC's worked example to the cent
- [ ] Cost per bid set reported and reviewed for two weeks before buying commitment pricing
- [ ] Data steward named (NFR-10) or R5 formally accepted in writing

---

*End of document. Version 3.0, 19 August 2026. Supersedes the five documents listed on page 1; those are retained unchanged in `docs/archive/` as the historical record.*
