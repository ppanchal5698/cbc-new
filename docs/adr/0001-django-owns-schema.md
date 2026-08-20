# ADR-0001 — Django owns the schema

**Status:** accepted · **Date:** 2026-08-20 · **Spec:** §3.2 rule 1, §8.2

## Context

Two services share one Postgres database: a Django API and a FastAPI pipeline worker.
Both write. Django ships a migration framework; SQLAlchemy has Alembic. Either could
own the schema, and letting each own its own tables is a third option that looks tidy
on a diagram.

## Decision

**All migrations are Django migrations.** The pipeline uses SQLAlchemy Core against
Django-migrated tables. There is no `Base.metadata.create_all` anywhere, and there is
no Alembic.

`tests/integration/test_schema_parity.py` reflects the live schema and asserts that
the SQLAlchemy definitions in `pipeline/db/tables.py` still match it, so drift fails
the build rather than production.

## Why

Two migration tools against one database means two sources of truth for a single
`ALTER TABLE`. The failure is not that it is confusing — it is that the tools cannot
see each other's history, so a column added by one is a column the other's
autogenerate proposes to drop. Recovering from that in production, with
`doc_elements` holding tens of thousands of rows per bid set and live citations
pointing at them, is not a recovery anyone should have to attempt.

Django wins the ownership rather than SQLAlchemy because the models carry the
constraints the traceability contract depends on: `ON DELETE RESTRICT` between
`field_provenance_elements` and `doc_elements` (ADR-0002), the unique
`(document_id, element_path)` that makes normalisation idempotent, and the choice
fields that keep enums honest. Those belong where the domain lives.

## Consequences

- The pipeline cannot create a table it needs. It has to be added to a Django model
  first. That friction is intentional.
- `pipeline/db/tables.py` mirrors only tables the pipeline touches by **raw SQL** —
  today just `openings_docelement`, written by `COPY`. Everything else goes through
  the Django ORM, which cannot drift from its own migrations.
- The parity test needs a real Postgres, so it is marked `integration` and runs in
  the compose stack and in CI.
