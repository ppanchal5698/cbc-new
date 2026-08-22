# CBC Copilot

Estimating and pricing copilot for Construction Building Components (CBC).

Upload an architectural bid set; get a reviewable, priced quote in which every
extracted value points back at the exact place on the exact page it came from.

## Documentation

Project docs live in this repository, not an external wiki.

| | |
|---|---|
| [Consolidated Spec](docs/CBC_Copilot_Consolidated_Spec.md) | The binding specification. Section numbers throughout the code refer to it. |
| [Architecture](docs/architecture.md) | Every AWS service the system uses, and the full journey of one bid set from upload to approved quote. |
| [Architecture decisions](docs/adr/) | Why the schema has one owner, why citations are a join table, why triage runs before OCR, why Cognito is deferred. |
| [AWS setup](docs/aws-setup.md) | Connecting an account, including the manual Bedrock model-access grant. |
| [backend/](backend/README.md) | Service layout and what is non-obvious about it. |
| [infra/](infra/README.md) | Terraform modules and environments. |
| [ops/runbooks/](ops/runbooks/) | Draining the DLQ, rotating secrets, restoring from a snapshot. |

## Local development

Needs Docker and nothing else.

```bash
cp .env.example .env
make up
```

That brings up Postgres, MiniStack (S3/SQS/SNS/SSM), the Django API, the
FastAPI worker, and the Next.js frontend, migrated and seeded. `make help` lists
every target.

The estimator's app — **Ops-Hub** — is at <http://localhost:3000>. Its layout and
design are a port of `html/HP Prototype v1.0 (1).html`; its types are generated
from the API schema by `make types` and are never hand-written.

Textract and Bedrock are the only services without a local substitute. `FAKE_OCR=1`
replays OCR from the PDF's own text layer, so the whole pipeline runs offline with
no AWS calls and no spend.

## The gates

Once it is up, the API is browsable and callable at **<http://localhost:8000/api/docs/>**
(Scalar). To try an authenticated endpoint: `POST /api/auth/token/` with your email
and password, then paste the key into the auth panel as `tokenAuth`.

```bash
make test         # full suite, including ★ schema parity against real Postgres
make eval         # golden-set quality: per-field precision, recall, absent-accuracy
make eval-check   # ...and fail on regression against the recorded baseline
make lint
make cost-report  # per-bid-set AWS cost attribution from live tables
make calibrate    # confidence threshold curve, for CBC to choose an operating point
```

`make eval` is the one worth looking at first. On the reference bid set it reports
100% recall on schedule pages at **$0.12 of OCR versus $0.98** for reading all 65
pages — which is the entire argument for [ADR-0003](docs/adr/0003-page-triage-before-ocr.md).

## What the system will not do

These are deliberate, and each has a test asserting it:

- It will not accept a value the model cannot cite to a real source element, and it
  will not accept a real citation for a value that is not present in the text it
  cites. Both are rejected and flagged, never repaired.
- It will not infer a fire rating or a door hand from a sibling row. If the schedule
  does not say it, the opening records `fire_rating_absent` and a flag.
- It will not use an LLM anywhere in matching or pricing. Those paths are
  deterministic and explainable.
