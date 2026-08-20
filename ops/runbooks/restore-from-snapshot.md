# Runbook — restore the database from a snapshot

**When:** data corruption, a bad migration, an accidental bulk delete, or a failed
instance.

**Read this before typing anything.** Restoring is not the only option and is
usually not the best one. Losing an afternoon of estimator review work to recover
from a problem affecting one project is a bad trade.

---

## First: is a restore the right move?

| Situation | Better option |
|---|---|
| One document's extraction is wrong | Re-run the pipeline for that document. Source PDFs are immutable in S3 (Object Lock GOVERNANCE) and are never the thing that is lost. |
| A bad migration | `python manage.py migrate <app> <previous>` if reversible. Check first. |
| One project's rows deleted | Point-in-time restore to a **side** instance, export those rows, import them. Never overwrite the live database to recover a subset. |
| Instance unreachable | Check RDS events first. A `t3.micro` out of CPU credits looks exactly like a dead instance and recovers on its own. |

A restore is right when the whole database is untrustworthy.

## What exists to restore from

- **Automated backups**, 7-day retention, with point-in-time recovery to any
  second in that window.
- **Manual snapshots**, taken before every migration in staging and production.

```bash
aws rds describe-db-snapshots --db-instance-identifier cbc-copilot-<env> \
  --query 'sort_by(DBSnapshots,&SnapshotCreateTime)[-5:].[DBSnapshotIdentifier,SnapshotCreateTime,Status]' \
  --output table

aws rds describe-db-instances --db-instance-identifier cbc-copilot-<env> \
  --query 'DBInstances[0].LatestRestorableTime'
```

## Restore

**Always restore to a new instance.** RDS cannot restore in place, and the damaged
instance is evidence.

```bash
# Point in time — prefer this; it loses the least.
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier cbc-copilot-<env> \
  --target-db-instance-identifier cbc-copilot-<env>-restore \
  --restore-time 2026-08-20T14:30:00Z \
  --db-subnet-group-name cbc-copilot-<env> \
  --vpc-security-group-ids sg-xxxxx --no-publicly-accessible
```

```bash
# Or from a named snapshot.
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier cbc-copilot-<env>-restore \
  --db-snapshot-identifier <snapshot-id> \
  --db-subnet-group-name cbc-copilot-<env> \
  --vpc-security-group-ids sg-xxxxx --no-publicly-accessible
```

Restores take 10–40 minutes. Wait:

```bash
aws rds wait db-instance-available --db-instance-identifier cbc-copilot-<env>-restore
```

## Verify before cutting over

Do not skip this. A restore to the wrong timestamp is discovered here or in
production.

```bash
psql "$RESTORED_URL" -c "SELECT max(created_at) FROM projects_document;"
psql "$RESTORED_URL" -c "SELECT count(*) FROM openings_opening;"
psql "$RESTORED_URL" -c "SELECT count(*) FROM openings_docelement;"
psql "$RESTORED_URL" -c "SELECT status, count(*) FROM quotes_quote GROUP BY status;"
```

Then confirm the schema matches the deployed code — a restore to before a migration
leaves the application pointing at columns that no longer exist:

```bash
DATABASE_URL="$RESTORED_URL" python manage.py migrate --plan
```

An empty plan is what you want.

## Cut over

1. **Stop the writers**, worker first so nothing is mid-transaction:
   ```bash
   sudo systemctl stop cbc-pipeline && sudo systemctl stop cbc-api
   ```
2. **Snapshot the damaged instance** before touching it. It is the only copy of
   whatever went wrong.
   ```bash
   aws rds create-db-snapshot --db-instance-identifier cbc-copilot-<env> \
     --db-snapshot-identifier cbc-copilot-<env>-incident-$(date +%Y%m%d%H%M)
   ```
3. **Rename.** Both instances reboot; expect a few minutes.
   ```bash
   aws rds modify-db-instance --db-instance-identifier cbc-copilot-<env> \
     --new-db-instance-identifier cbc-copilot-<env>-damaged --apply-immediately
   aws rds modify-db-instance --db-instance-identifier cbc-copilot-<env>-restore \
     --new-db-instance-identifier cbc-copilot-<env> --apply-immediately
   ```
   Renaming beats editing `DATABASE_URL` in SSM: the endpoint hostname follows the
   identifier, so nothing else has to change and there is no stale connection string
   left anywhere.
4. **Start** the API, verify health, then the worker.

## Afterwards

S3 is unaffected — source documents are versioned and Object Lock'd, and derived
artefacts are reproducible. But rows written after the restore point are gone, and
the derived objects they described are now orphaned.

```sql
-- Documents whose pipeline never finished. Re-enqueue these.
SELECT id, filename, status FROM projects_document
WHERE status NOT IN ('READY_FOR_REVIEW', 'FAILED');
```

Re-run the pipeline for each. It is idempotent: positional `element_path` means
re-normalising reproduces identical element identities, so citations survive.

**Tell the estimators what window of review work was lost.** They are the only ones
who can redo it, and they cannot if nobody says so.

## Close out

- [ ] Application healthy against the restored instance
- [ ] `migrate --plan` empty
- [ ] Incident snapshot of the damaged instance retained
- [ ] Damaged instance deleted only after the incident is closed
- [ ] Unfinished documents re-enqueued
- [ ] Lost-work window communicated
- [ ] Backup retention reviewed if 7 days was not enough
