# Infrastructure

Terraform, laid out per §8.1: reusable `modules/`, one root per environment in
`envs/`.

```
modules/
  network/        VPC, subnets, S3 gateway endpoint (no NAT)
  storage/        source bucket (Object Lock GOVERNANCE), derived bucket, lifecycle
  database/       RDS Postgres 17, subnet group, parameter group
  queue/          SQS + DLQ + redrive, SNS topic, Textract publish role
  compute/        EC2 api + worker hosts, security group, instance profiles
  cdn/            CloudFront over the derived bucket
  ai/             Bedrock + Textract IAM, SSM parameters, break-glass role
  observability/  log groups, alarms, budget, cost anomaly detection

envs/
  dev/            free-tier shaped — APPLIED
  staging/        §3.1 sizing — written and validated, not applied
  prod/           §3.1 sizing + Multi-AZ — written and validated, not applied
```

First-time account setup, including the manual Bedrock grant, is
[`../docs/aws-setup.md`](../docs/aws-setup.md).

## Environments

| | dev | staging | prod |
|---|---|---|---|
| API host | `t3.micro` | `t4g.large` | `t4g.large` |
| Worker host | `t3.micro` | `t4g.medium` | `t4g.medium` |
| Database | `db.t3.micro` | `db.t4g.medium` | `db.t4g.medium`, Multi-AZ |
| Backups | 7 days | 7 days | 35 days |
| Budget | $10 | $250 | $400 |
| Deletion protection | off | on | on |

staging and prod are `t4g` — Graviton, roughly 19% cheaper for equivalent capacity
(§10.3), which is why the Dockerfiles target `linux/arm64`. dev is `t3.micro`
because that is the free-tier eligible x86 size.

The worker is a **separate host** from the API. §9 B1: its memory profile is spiky
— a 200-page set, PyMuPDF rasterisation, and buffered OCR JSON all peak together —
and colocating it means one large bid set degrades every estimator's page loads.

## Usage

```bash
make plan-dev     # init + plan
make apply-dev    # review the plan first
make fmt-tf
```

Always plan, read the plan, then apply. There is no target that applies without
one.

## Three things that are deliberately not what they look like

**Object Lock retention is unset.** The source bucket is created with Object Lock
*enabled* — that cannot be turned on afterwards — but with no default retention
rule. §11.3 requires the period signed off in writing first, and it cannot be
shortened for objects already written under it.
`terraform output object_lock_retention_configured` returns false until then, so a
deploy cannot quietly ship without it.

**Bedrock model IDs are not in Terraform.** They are resolved at deploy time from
`ListFoundationModels` / `ListInferenceProfiles` and written to SSM by
`ops/scripts/resolve_bedrock_models.py` (C5/D12). Terraform managing them would put
a hardcoded model id in version control, which is the thing C5 forbids. The IAM
policy grants *any* Anthropic model plus the account's inference profiles, so a
model change needs no apply.

**There is no NAT gateway and no SSH ingress.** S3 traffic leaves via the free
gateway endpoint; nothing else needs outbound internet from a private subnet.
Shell access is SSM Session Manager — no open port, no key pair, no bastion, and
the session is logged.

## The break-glass role

`<prefix>-break-glass-object-lock` can override GOVERNANCE retention on the source
bucket. It requires MFA, is assumable only from within the account, and every use
raises a CloudWatch alarm.

It exists because GOVERNANCE is only meaningfully different from COMPLIANCE if
someone *can* override it — for a genuine mistaken upload or a deletion request.
COMPLIANCE cannot be overridden by anyone including root, for the full retention
period, which would make one bad upload permanent.

## Cost

Guards are in before the first Textract call, not after:

- AWS Budgets at the environment limit, alerting at 50/80/100% of actual **and** on
  forecast.
- Cost Anomaly Detection, daily, $10 absolute impact — catches a spike inside an
  otherwise normal month, which a budget cannot.
- `MAX_OCR_COST_PER_DOCUMENT_USD` in SSM, checked in the application **before** any
  OCR call. This is the one that catches a mistaken 3,000-page upload.
- Page triage (`config/ocr_routes.json`): measured at $0.12 versus $0.98 on the
  reference 65-page bid set.

Cost allocation tags (`env`, `service`, `costCenter`) are applied as provider
default tags. **Activate them once in Billing → Cost allocation tags**, or the
per-service breakdown never appears and `ops/scripts/cost_report.py` has nothing
to reconcile against.

For prod, §10.3 puts a one-year no-upfront Compute Savings Plan at roughly 28% off
the two EC2 hosts. Buy it after the shape has held for a month — a Savings Plan on
the wrong instance family is a year of paying for capacity nobody uses.
