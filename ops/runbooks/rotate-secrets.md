# Runbook — rotate secrets

**When:** on the quarterly schedule, on any staff change with production access, and
immediately on suspected exposure.

Every secret lives in SSM Parameter Store as a `SecureString` (§8.4). Nothing is in
`.env` outside local development, and nothing is in the image. If you find a secret
in either, that is the incident — rotating the value does not fix it.

---

## What there is

| Parameter | What it is | Rotation |
|---|---|---|
| `/cbc-copilot/<env>/SECRET_KEY` | Django signing key | Invalidates every session. Users re-login. |
| `/cbc-copilot/<env>/DATABASE_URL` | RDS credentials | Two-phase, below. |
| `/cbc-copilot/<env>/*` (others) | Config, not secret | No rotation needed. |

IAM roles carry no long-lived keys — the EC2 instance profile issues temporary
credentials. There is nothing to rotate for AWS access, which is the point of not
using access keys.

## Django SECRET_KEY

```bash
NEW=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
aws ssm put-parameter --name "/cbc-copilot/<env>/SECRET_KEY" \
  --value "$NEW" --type SecureString --overwrite
```

Config is read at process start, so restart the API:

```bash
sudo systemctl restart cbc-api
```

Verify a login works before walking away. Sessions issued under the old key are dead;
announce it if estimators are mid-review.

## Database credentials

Two-phase, because a single-phase swap disconnects running workers mid-transaction.

**Phase 1 — add the new user.** Postgres allows two valid logins at once, which is
what makes this zero-downtime.

```sql
CREATE USER cbc_app_v2 WITH PASSWORD '<generated>';
GRANT ALL PRIVILEGES ON DATABASE cbccopilot TO cbc_app_v2;
GRANT ALL ON ALL TABLES IN SCHEMA public TO cbc_app_v2;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO cbc_app_v2;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO cbc_app_v2;
```

That last line matters: without it the next Django migration creates tables the new
user cannot read, and the failure surfaces days later.

**Phase 2 — cut over, one host at a time.**

```bash
aws ssm put-parameter --name "/cbc-copilot/<env>/DATABASE_URL" \
  --value "postgresql://cbc_app_v2:<pw>@<host>:5432/cbccopilot" \
  --type SecureString --overwrite

sudo systemctl restart cbc-api        # verify health, then:
sudo systemctl restart cbc-pipeline
```

Drain the worker before restarting it, or in-flight messages return to the queue and
retry — correct, but it burns an attempt against `maxReceiveCount` for no reason.

**Phase 3 — after 24h clean**, drop the old user. Not sooner: the old credential is
the rollback.

```sql
DROP OWNED BY cbc_app; DROP USER cbc_app;
```

## Bedrock and Textract

No secrets. Access is the instance profile's IAM policy. To revoke, change the policy —
there is no key to rotate.

## On suspected exposure

Rotation is step three, not step one.

1. **Revoke first.** Detach the IAM policy or disable the credential. A rotated secret
   that the attacker already used is not contained.
2. **Look at what was done.** CloudTrail for API calls, `feedback` and
   `pipeline_jobs` for data touched. Object Lock GOVERNANCE means source documents
   cannot have been altered — check CloudTrail for
   `s3:BypassGovernanceRetention` to confirm the break-glass role was not used.
3. Rotate as above.
4. Rotate *everything* in that environment, not just the exposed value.
5. Write it up. Include how it was exposed, or it will happen again.

## Close out

- [ ] New value in SSM as `SecureString`
- [ ] API and pipeline restarted and healthy
- [ ] A real login and a real document upload both succeed
- [ ] Old credential removed after the soak
- [ ] Rotation date recorded
