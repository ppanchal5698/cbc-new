# CI

**The workflows are in [`.github/workflows/`](../../.github/workflows/), not here.**

§8.1 places them under `ops/ci/`. GitHub Actions only executes workflows from
`.github/workflows/` — a `.yml` in this directory is an inert file that looks like
CI, which is worse than no file at all. The layout is followed everywhere it can be;
this is the one place it cannot be.

| Workflow | Gate |
|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml) | ruff · pytest incl. ★ schema parity · golden-set quality gate · `terraform fmt` and `validate` |

## Repository configuration this expects

| Setting | Kind | Purpose |
|---|---|---|
| `GOLDEN_SET_ROLE_ARN` | Actions **variable** | OIDC role with read access to `golden/` in the derived bucket. Without it the quality-gate job emits a warning annotation and the build is green on lint and tests only — visibly, never silently. |

No long-lived AWS keys. The role is assumed via GitHub's OIDC provider, so there is
nothing to rotate (see [`../runbooks/rotate-secrets.md`](../runbooks/rotate-secrets.md)).

## Running the same gates locally

```bash
make lint
make test
make eval-check
```
