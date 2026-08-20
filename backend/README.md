# CBC Copilot — backend

Two services, one database.

| | |
|---|---|
| `api/` | Django + DRF. Owns the schema (ADR-0001), the review and approval surface, and quote export. |
| `pipeline/` | FastAPI worker. Consumes SQS, runs preprocess → OCR → normalise → extract → link → match → price. |
| `shared/` | Imported by both: `enums.py`, `s3_keys.py`, `config.py`. One definition of each, never two. |
| `config/` | `ocr_routes.json` — the OCR routing table as data, not code (Risk R1). |
| `tests/golden/` | The quality gate: labels, harness, recorded baseline. |

Everything runs in the compose stack from the repo root. `make help` lists the
targets; the ones that matter are `make up`, `make test`, `make eval`.

## Layout notes that are not obvious

**`shared/config.py` refuses to start on a missing required variable** (§8.4). There
is no SQLite fallback and no defaulted `BEDROCK_MODEL_ID`. A model ID is resolved at
deploy time and pinned in SSM (`make bedrock-resolve`), because a run that cannot name
its exact model version cannot be audited.

**`pipeline/db/tables.py` mirrors only tables written by raw SQL** — today just
`openings_docelement`, via `COPY`. `tests/integration/test_schema_parity.py` fails the
build if it drifts from the Django migrations.

**`pipeline/llm/prompts/` is versioned and never edited in place.** `v1.md` stays
`v1.md`; a change is `v2.md`. `extraction_runs.prompt_version` records which one
produced a value, so a quote from six months ago can still be explained.

## Running without AWS

`FAKE_OCR=1` replays OCR from the PDF's own text layer, so the whole pipeline runs
offline with no Textract and no spend. MiniStack covers S3, SQS, SNS, and SSM. Bedrock
is the only service with no local substitute; extraction needs either real credentials
or the `local-llm` extra.

## Tests

```bash
make test        # everything, including the schema-parity gate
make test-fast   # skips anything marked integration
```

Two markers: `integration` needs the stack up; `aws` makes real AWS calls and is
skipped unless `CBC_ALLOW_AWS_TESTS=1`.

The tests worth knowing about before changing extraction are in
`tests/test_traceability_contract.py`. They assert that a fabricated `element_id` is
rejected, and that a *real* element id cited for a value not present in it is **also**
rejected. Those two are the contract; if they fail, nothing downstream means anything.

## Where the specification lives

[`../docs/CBC_Copilot_Consolidated_Spec.md`](../docs/CBC_Copilot_Consolidated_Spec.md)
is binding. Decisions that resolve a conflict in it are recorded in
[`../docs/adr/`](../docs/adr/).
