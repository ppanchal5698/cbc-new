# Runbook — drain the dead-letter queue

**Alarm:** `cbc-copilot-<env>-dlq-depth` — `ApproximateNumberOfMessagesVisible > 0`
on `document-ready-dlq`.

**Severity:** a message here means a bid set was uploaded and never processed. The
estimator has no idea: from the UI the document sits in `PROCESSING` forever. Treat
as customer-visible.

**Do not purge the queue.** The messages are the only record of what failed.

---

## 1. What is in there

```bash
aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names All
```

Read messages without consuming them — a long visibility timeout on peek, so a
redrive later still sees them:

```bash
aws sqs receive-message --queue-url "$DLQ_URL" --max-number-of-messages 10 --visibility-timeout 5
```

Each body carries `document_id`. That is the join key for everything below.

## 2. Why it failed

A message lands here after `SQS_MAX_RECEIVE_COUNT` (3) failed deliveries. The
worker records the reason before giving up:

```sql
SELECT stage, status, attempt, error_detail, external_job_id, updated_at
FROM projects_pipelinejob
WHERE document_id = '<document_id>'
ORDER BY updated_at DESC;
```

```bash
aws logs start-query \
  --log-group-name "/cbc-copilot/<env>/pipeline" \
  --start-time "$(date -d '24 hours ago' +%s)" --end-time "$(date +%s)" \
  --query-string 'fields @timestamp, stage, message, error_detail
                  | filter document_id = "<document_id>" | sort @timestamp desc'
```

### The four causes seen so far

| Symptom | Cause | Action |
|---|---|---|
| `EncryptedDocument` | Password-protected PDF | Not retryable. Ask for an unprotected copy; mark the document `FAILED` with that reason so the estimator sees it. |
| `BudgetExceeded` | Estimated OCR cost over `MAX_OCR_COST_PER_DOCUMENT_USD` | **Nothing has been spent.** Confirm the right document was uploaded. If a 3,000-page set is genuinely intended, raise the guard *deliberately* in SSM, then redrive. |
| `ThrottlingException` from Textract | Concurrent-job limit | Transient. Redrive; if it recurs, request a Textract quota increase rather than raising `maxReceiveCount`. |
| DB connection refused | RDS restarted or maxed connections | Fix the database first. Redriving into a down database just refills the DLQ. |

## 3. Confirm no double spend

A redelivered OCR job that re-submitted to Textract is billed twice. The guard is
`pipeline_jobs.external_job_id`, written before the call is treated as complete.

```sql
SELECT stage, attempt, external_job_id, cost_estimate, cost_actual
FROM projects_pipelinejob WHERE document_id = '<document_id>';
```

`attempt > 1` with a **stable** `external_job_id` means the guard held. A *different*
job id per attempt means it did not — that is a bug, not an ops problem. File it and
attach these rows.

`ops/scripts/cost_report.py --project-id <id>` flags retried jobs for the same reason.

## 4. Redrive

Only after the cause is fixed or is known to be transient.

```bash
aws sqs start-message-move-task \
  --source-arn "$DLQ_ARN" --destination-arn "$MAIN_QUEUE_ARN" \
  --max-number-of-messages-per-second 5
```

Rate-limited on purpose: dumping a backlog into a `t3.micro` worker recreates the
outage. Watch it:

```bash
aws sqs list-message-move-tasks --source-arn "$DLQ_ARN"
```

## 5. Poison messages

A message that fails identically after a fixed cause is poison. Do not let it cycle.

1. Copy the body into the incident ticket.
2. Set the document to `QUARANTINED` (`PipelineJobStatus.QUARANTINED` exists for this):
   ```sql
   UPDATE projects_pipelinejob SET status = 'QUARANTINED', error_detail = '<ticket>'
   WHERE document_id = '<document_id>';
   ```
3. Delete that one message by receipt handle — never `purge-queue`.

## 6. Close out

- [ ] DLQ depth back to 0 and the alarm cleared
- [ ] Every affected document reached `READY_FOR_REVIEW` or an explicit `FAILED` with a reason the estimator can read
- [ ] `cost_report.py` shows no unexplained duplicate spend
- [ ] If the cause was a code defect, a test reproduces it before the fix ships
