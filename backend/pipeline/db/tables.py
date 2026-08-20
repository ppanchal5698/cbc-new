"""
SQLAlchemy Core mirror of the Django-owned schema (§8.1, §8.2).

Django owns every migration (ADR-0001). This module never creates anything —
there is no ``Base.metadata.create_all`` here and there must never be one. It
exists so the pipeline's **raw-SQL** writes have a declared shape that
``tests/integration/test_schema_parity.py`` can diff against the live database,
turning schema drift into a red build instead of a production ``COPY`` that
silently writes a column into the wrong slot.

Only tables the pipeline touches by raw SQL are mirrored. Every other write goes
through the Django ORM, which already fails loudly on drift because the model and
the migration are the same source.

Today that is exactly one table: ``openings_docelement``, written by
``COPY`` in :mod:`pipeline.stages.normalize` (bottleneck B3).

ponytail: one table because one raw-SQL path exists. A second is ~10 lines here
plus its name in ``MIRRORED_TABLES`` — the parity test picks it up automatically.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

#: ``doc_elements`` (§7.2). The one bulk path.
#:
#: **Column order is load-bearing.** ``normalize.ELEMENT_COLUMNS`` is derived from
#: this definition and fed straight into ``COPY openings_docelement (...)``, so the
#: order here is the order values are written in. Reordering these lines reorders
#: the COPY; the parity test catches a missing or renamed column, but only the
#: single derivation below stops the two from disagreeing in the first place.
doc_elements = Table(
    "openings_docelement",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("element_path", String(255), nullable=False),
    Column("page_number", Integer, nullable=False),
    Column("element_type", String(50), nullable=False),
    Column("text", Text, nullable=False),
    # Four vertices, as Textract reports them: a polygon, not a rectangle, because
    # a rotated sheet's text is not axis-aligned and the overlay must still land on
    # the words (§7.2).
    Column("x0", Float),
    Column("y0", Float),
    Column("x1", Float),
    Column("y1", Float),
    Column("x2", Float),
    Column("y2", Float),
    Column("x3", Float),
    Column("y3", Float),
    # Axis-aligned envelope, denormalised so a "what is near this point" query is an
    # index scan rather than a polygon computation.
    Column("bbox_x_min", Float),
    Column("bbox_y_min", Float),
    Column("bbox_x_max", Float),
    Column("bbox_y_max", Float),
    Column("ocr_confidence", Float),
    Column("reading_order", Integer),
    Column("table_id", UUID(as_uuid=True)),
    Column("row_index", Integer),
    Column("col_index", Integer),
    Column("column_header", Boolean),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

#: Every table the parity test checks. Add a table here when a new raw-SQL path
#: appears, never because a Django model was added.
MIRRORED_TABLES: tuple[Table, ...] = (doc_elements,)


def column_names(table: Table) -> tuple[str, ...]:
    """Declared column names, in declaration order — the COPY column list."""
    return tuple(column.name for column in table.columns)
