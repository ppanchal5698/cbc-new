# ADR-0002 — Citations are a join table, not a `uuid[]`

**Status:** accepted · **Date:** 2026-08-20 · **Spec:** conflict C8, §7.2

## Context

`field_provenance` records which source elements an extracted value came from. Two
shapes were specified across the source documents: a `source_element_ids uuid[]`
column, and a `field_provenance_elements` join table with a real foreign key.

Postgres supports array columns well. The array is one fewer table, one fewer join,
and a single-row read.

## Decision

**A join table.** `field_provenance_elements(field_provenance_id, doc_element_id,
ordinal)`, with `ON DELETE CASCADE` to the provenance and **`ON DELETE RESTRICT`** to
`doc_elements`.

## Why

The traceability contract is: *if the model cannot point to a real element, the field
is rejected.* That is the load-bearing property of the whole system — it is what makes
an extracted value auditable rather than merely plausible.

An array column carries no referential integrity. A `uuid[]` can hold an id that never
existed, or one that existed and was deleted, and Postgres stores it without
complaint. The contract would then be enforced only by `pipeline/stages/link.py` —
that is, by application code, which can be bypassed by a backfill script, a data fix,
or the next service someone adds.

`ON DELETE RESTRICT` is the specific reason this matters in practice. Re-normalising a
document deletes and re-inserts its `doc_elements`. If an estimator has already
reviewed citations against those elements, the delete **fails**, and that is the
correct outcome: silently invalidating reviewed work is worse than an error. With an
array column there is nothing to fail — the ids simply stop resolving, and nobody
finds out until someone clicks a citation.

`ordinal` also comes free, where the array gives it only by convention.

## Consequences

- One extra join to read a field's citations. Negligible: the query is indexed and
  bounded by the handful of elements one value cites.
- Bulk-deleting elements requires deleting dependent provenance first, deliberately.
- The gate in `link.py` becomes a second line of defence rather than the only one.
