"""
★ The schema-drift gate (§8.2).

    "One integration test asserts the FastAPI table definitions match the live
    schema, so drift fails CI rather than production."

Django owns every migration (ADR-0001). The pipeline writes ``doc_elements`` with
raw ``COPY``, which is positional: it names columns, but nothing at runtime checks
that those columns still exist, still mean the same thing, or are still in the
order the writer assumes. A migration that renames ``col_index`` does not break the
Django ORM and does not break import — it breaks the first 200-page bid set to hit
production, silently, hours later.

This test closes that gap by reflecting the live database and diffing it against
:mod:`pipeline.db.tables`. It needs a real Postgres, so it is marked
``integration`` and runs inside the compose stack (``make test``).
"""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from sqlalchemy import create_engine, inspect

from pipeline.db.tables import MIRRORED_TABLES

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def inspector():
    """
    Reflector over the database Django is currently pointed at.

    Under pytest-django that is the migrated *test* database, which is the right
    target: it is built from the migrations on this branch, so the test fails on
    drift the moment a migration is written, not once it reaches an environment.

    Disposed rather than leaked — a live SQLAlchemy pool holds sessions open and
    Django cannot then drop the test database at teardown.
    """
    db = django_settings.DATABASES["default"]
    engine = create_engine(
        f"postgresql+psycopg://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    try:
        yield inspect(engine)
    finally:
        engine.dispose()


#: Postgres type names that satisfy a given SQLAlchemy column type. Compared
#: loosely on purpose: the mirror exists to catch a column that vanished, was
#: renamed, or changed category — not to police ``varchar(255)`` against
#: ``varchar(256)``, which no COPY can tell apart anyway.
TYPE_FAMILIES = {
    "UUID": {"uuid"},
    "STRING": {"varchar", "character varying", "text"},
    "TEXT": {"text", "varchar", "character varying"},
    "INTEGER": {"integer", "int4", "bigint", "int8", "smallint"},
    "FLOAT": {"double precision", "float8", "real", "float4", "numeric"},
    "BOOLEAN": {"boolean", "bool"},
    "DATETIME": {"timestamp with time zone", "timestamptz", "timestamp"},
}


def _family(sa_type) -> set[str]:
    name = type(sa_type).__name__.upper()
    for key, accepted in TYPE_FAMILIES.items():
        if key in name:
            return accepted
    raise AssertionError(f"no type family declared for {name}; add one to TYPE_FAMILIES")


@pytest.mark.parametrize("table", MIRRORED_TABLES, ids=lambda t: t.name)
def test_mirrored_table_exists(table, inspector):
    assert inspector.has_table(table.name), (
        f"{table.name} is mirrored in pipeline/db/tables.py but does not exist in the "
        "live schema. Either a migration dropped or renamed it, or the mirror is stale."
    )


@pytest.mark.parametrize("table", MIRRORED_TABLES, ids=lambda t: t.name)
def test_mirrored_columns_exist_in_live_schema(table, inspector):
    """
    Every declared column must exist. This is the direction that matters: the
    COPY names these columns, so a missing one is an immediate runtime failure.
    """
    live = {c["name"] for c in inspector.get_columns(table.name)}
    declared = {c.name for c in table.columns}

    missing = sorted(declared - live)
    assert not missing, (
        f"{table.name}: declared in pipeline/db/tables.py but absent from the live "
        f"schema: {missing}. A Django migration changed the table without the mirror "
        "following. Update pipeline/db/tables.py — the COPY writes these column names."
    )


@pytest.mark.parametrize("table", MIRRORED_TABLES, ids=lambda t: t.name)
def test_no_unmirrored_columns(table, inspector):
    """
    The other direction. A new column is not a runtime failure for the COPY, but it
    is drift: the pipeline is writing rows that leave it null, and nobody decided
    that. Fail here so the choice is explicit.
    """
    live = {c["name"] for c in inspector.get_columns(table.name)}
    declared = {c.name for c in table.columns}

    extra = sorted(live - declared)
    assert not extra, (
        f"{table.name}: present in the live schema but not mirrored: {extra}. "
        "A migration added these. Add them to pipeline/db/tables.py (and so to the "
        "COPY) or record why the pipeline deliberately leaves them unset."
    )


@pytest.mark.parametrize("table", MIRRORED_TABLES, ids=lambda t: t.name)
def test_mirrored_column_types_match(table, inspector):
    live = {c["name"]: str(c["type"]).lower() for c in inspector.get_columns(table.name)}

    mismatched = []
    for column in table.columns:
        actual = live.get(column.name)
        if actual is None:
            continue  # covered by test_mirrored_columns_exist_in_live_schema
        accepted = _family(column.type)
        if not any(candidate in actual for candidate in accepted):
            mismatched.append(f"{column.name}: live={actual!r} declared={type(column.type).__name__}")

    assert not mismatched, f"{table.name} type drift:\n  " + "\n  ".join(mismatched)


@pytest.mark.parametrize("table", MIRRORED_TABLES, ids=lambda t: t.name)
def test_mirrored_nullability_matches(table, inspector):
    """
    A column the mirror thinks is nullable but the database requires is a COPY that
    fails on the first row with a null there — for a bid set, hours into a run.
    """
    live = {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)}

    mismatched = [
        f"{c.name}: live nullable={live[c.name]} declared nullable={c.nullable}"
        for c in table.columns
        if c.name in live and not c.primary_key and live[c.name] != c.nullable
    ]
    assert not mismatched, f"{table.name} nullability drift:\n  " + "\n  ".join(mismatched)


def test_copy_statement_uses_the_mirrored_column_list():
    """
    The reason the mirror is worth having: ``normalize`` must not carry its own
    hand-written column list that can silently diverge from this one.
    """
    from pipeline.db.tables import column_names, doc_elements
    from pipeline.stages.normalize import COPY_SQL, ELEMENT_COLUMNS

    assert ELEMENT_COLUMNS == column_names(doc_elements)
    assert COPY_SQL == f"COPY {doc_elements.name} ({', '.join(ELEMENT_COLUMNS)}) FROM STDIN"
