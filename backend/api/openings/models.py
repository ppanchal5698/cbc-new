"""
Elements, extraction runs, provenance, openings, and matches (§7.2, §7.3, §7.4).

This module holds the traceability contract in database form:

    Textract produces deterministic geometry. The model produces semantic
    interpretation and must cite Textract-normalised element IDs for every field
    it emits. A field whose citation cannot be validated is rejected, not
    repaired. "Show me the source" is a database join, never a second inference.

Every foreign key below exists so that sentence is enforced by the database rather
than by application code that might one day be bypassed.
"""

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from projects.models import Document, Project, TimestampedModel

from shared.enums import (
    ElementType,
    ExtractionRunStatus,
    FireRatingLocation,
    Handing,
    MatchStatus,
    ReviewState,
)


class DocElement(TimestampedModel):
    """
    Every word, line, table cell, and selection mark from OCR (§7.2).

    Renamed from ``di_elements`` (C9/D15) — the ``di_`` prefix was a vestigial
    Azure Document Intelligence name that three separate documents apologised for.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="The element_id the model cites.",
    )
    # Keyed to the document, not the bid: a bid set can be several PDFs.
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="elements")

    element_path = models.CharField(
        max_length=255,
        help_text=(
            "Positional pointer, e.g. 'pages/3/words/412' or 'tables/0/cells/17'. "
            "NATURAL KEY. Textract mints a fresh Block.Id on every job, so positional "
            "paths are what make normalisation idempotent and let a re-run re-link "
            "deterministically instead of orphaning citations (§7.2)."
        ),
    )
    page_number = models.IntegerField(
        help_text="Document-global, after any split-part offset is applied (§4.6)."
    )
    element_type = models.CharField(max_length=50, choices=ElementType.choices())
    text = models.TextField(blank=True)

    # Polygon vertices as 0-1 page fractions. Eight real columns, not JSONB:
    # JSONB stored roughly 100+ bytes of structure for 32 bytes of data, per
    # element, at tens of thousands of elements per bid set (bottleneck B4).
    # 0-1 fractions map directly to CSS percentages, which is what lets the
    # viewer overlay the highlight client-side with no server geometry (B5).
    x0 = models.FloatField(null=True, blank=True)
    y0 = models.FloatField(null=True, blank=True)
    x1 = models.FloatField(null=True, blank=True)
    y1 = models.FloatField(null=True, blank=True)
    x2 = models.FloatField(null=True, blank=True)
    y2 = models.FloatField(null=True, blank=True)
    x3 = models.FloatField(null=True, blank=True)
    y3 = models.FloatField(null=True, blank=True)

    # Derived and indexed — supports spatial filtering without unpacking the polygon.
    bbox_x_min = models.FloatField(null=True, blank=True)
    bbox_y_min = models.FloatField(null=True, blank=True)
    bbox_x_max = models.FloatField(null=True, blank=True)
    bbox_y_max = models.FloatField(null=True, blank=True)

    ocr_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Scaled 0-1 from Textract's 0-100. NEVER recomputed, never overwritten — "
            "it is one half of the composite confidence and must stay attributable."
        ),
    )
    reading_order = models.IntegerField(null=True, blank=True)

    table_id = models.UUIDField(
        null=True, blank=True, help_text="Groups the cells of one table; scopes extraction batches."
    )
    row_index = models.IntegerField(
        null=True, blank=True, help_text="0-indexed (Textract is 1-indexed; normalised down)."
    )
    col_index = models.IntegerField(null=True, blank=True)
    column_header = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "element_path"], name="uniq_element_path_per_document"
            )
        ]
        indexes = [
            models.Index(fields=["document", "page_number"]),
            models.Index(fields=["table_id"]),
            models.Index(fields=["document", "table_id", "row_index", "col_index"]),
            models.Index(fields=["bbox_x_min", "bbox_y_min", "bbox_x_max", "bbox_y_max"]),
        ]

    def __str__(self) -> str:
        return f"{self.element_path}: {self.text[:40]}"


class ExtractionRun(TimestampedModel):
    """
    One extraction attempt over one document (§7.2).

    Exists so a re-extraction never clobbers a prior one an estimator has already
    reviewed, and so every extracted value is attributable to an exact, reproducible
    configuration (NFR-3).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="extraction_runs"
    )
    model_id = models.CharField(
        max_length=255,
        help_text=(
            "The RESOLVED Bedrock model or inference-profile ID (C5). Never a "
            "hardcoded string: a run that cannot name its exact model version "
            "cannot be audited."
        ),
    )
    model_id_cheap = models.CharField(
        max_length=255, blank=True, help_text="Haiku tier used for Pass A locate (§5.3)."
    )
    prompt_version = models.CharField(
        max_length=100, help_text="Resolves to llm/prompts/extraction/<version>.md — never edited in place."
    )
    inference_params = models.JSONField(
        default=dict, help_text="temperature, top_p, max_tokens. temperature is 0 (§5.4)."
    )
    ocr_result_version_id = models.CharField(max_length=1024, null=True, blank=True)
    route_config_version = models.CharField(
        max_length=64, blank=True, help_text="Hash of the OCR routing table in force (§4.4)."
    )

    status = models.CharField(
        max_length=50,
        choices=ExtractionRunStatus.choices(),
        default=ExtractionRunStatus.STARTED.value,
    )
    error_detail = models.TextField(blank=True)

    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cached_input_tokens = models.IntegerField(
        default=0, help_text="Prompt-cache hits; billed at roughly a tenth of standard (§5.12)."
    )
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["document", "status"]),
            models.Index(fields=["prompt_version", "model_id"]),
        ]

    def __str__(self) -> str:
        return f"run {self.id} {self.prompt_version}@{self.model_id}"


class Opening(TimestampedModel):
    """
    One door location (FR-2, §7.3).

    Every field below has a corresponding ``FieldProvenance`` row: **no value
    reaches an estimator without one.**
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="openings")
    extraction_run = models.ForeignKey(
        ExtractionRun, on_delete=models.CASCADE, related_name="openings"
    )

    door_number = models.CharField(max_length=100, help_text="The key everything hangs off.")

    # -- size: fixed 4-digit rule, parsed by code not by the model (§5.7) ------
    size_raw = models.CharField(
        max_length=32, null=True, blank=True, help_text="As written, e.g. '3070'."
    )
    width_inches = models.IntegerField(null=True, blank=True)
    height_inches = models.IntegerField(null=True, blank=True)

    # -- handing: zero-tolerance (§5.8) ---------------------------------------
    handing = models.CharField(max_length=10, choices=Handing.choices(), null=True, blank=True)
    handing_absent = models.BooleanField(
        default=False,
        help_text=(
            "EXPLICIT. 'No handing found' must be distinguishable from 'not yet "
            "extracted' and from null — a null cannot carry three states (FR-8)."
        ),
    )

    # -- finish: dual nomenclature (NR-3) -------------------------------------
    finish_raw = models.CharField(max_length=64, null=True, blank=True)
    finish_code = models.ForeignKey(
        "pricing.FinishCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="openings",
        help_text="US19 and US26D must never collapse to the same row (§1.3).",
    )

    # -- fire rating: zero-tolerance (§5.8) -----------------------------------
    fire_rating_raw = models.CharField(
        max_length=64, null=True, blank=True, help_text="As written: '90 MIN', '1-1/2 HR', 'B LABEL'."
    )
    fire_rating_minutes = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(120)],
        help_text="20 / 45 / 60 / 90 only. Unrecognised flags; NEVER defaults to unrated.",
    )
    fire_rating_absent = models.BooleanField(default=False)
    fire_rating_source_location = models.CharField(
        max_length=50,
        choices=FireRatingLocation.choices(),
        default=FireRatingLocation.UNKNOWN.value,
        help_text=(
            "Accumulates the empirical answer to Open Item 9 — CBC has not said where "
            "ratings live on their bid sets, so the system observes it (§5.8, Risk R1)."
        ),
    )

    hardware_group = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="'HW-3', or an explicit manufacturer part/series callout — both are normal (§1.3).",
    )

    alternate_designation = models.CharField(
        max_length=100, null=True, blank=True, help_text="Free text as written."
    )
    bid_alternate = models.ForeignKey(
        "projects.BidAlternate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="openings",
        help_text="Flag-gated (§7.6). Populated but not reconciled until Open Item 11 lands.",
    )

    wall_type = models.CharField(max_length=100, null=True, blank=True)
    throat_depth = models.ForeignKey(
        "pricing.ThroatDepth",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="openings",
    )

    review_state = models.CharField(
        max_length=50, choices=ReviewState.choices(), default=ReviewState.AUTO.value, db_index=True
    )
    review_notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "extraction_run", "door_number"], name="uniq_opening_per_run"
            ),
            # A rating cannot simultaneously be present and declared absent.
            # Enforced in the database because "absent" is a safety claim (§5.8).
            models.CheckConstraint(
                condition=models.Q(fire_rating_absent=False)
                | models.Q(fire_rating_minutes__isnull=True),
                name="ck_rating_absent_excludes_value",
            ),
            models.CheckConstraint(
                condition=models.Q(handing_absent=False) | models.Q(handing__isnull=True),
                name="ck_handing_absent_excludes_value",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "door_number"]),
            models.Index(fields=["extraction_run", "door_number"]),
            models.Index(fields=["project", "review_state"]),
        ]

    def __str__(self) -> str:
        return f"Opening {self.door_number}"


class HardwareSetComponent(TimestampedModel):
    """
    One component of a resolved hardware set (§5.11, FR-4).

    A door schedule says ``HW-3``; the Division 08 spec section defines what
    ``HW-3`` contains. Joining them is a separate model call with its own narrow
    context, and this is where its answer lands — one row per component, each with
    its own ``field_provenance`` citations.

    **An unresolved callout is a row too.** ``resolved=False`` with no components
    is the honest record that the door schedule referenced a set whose definition
    is not in this bid set. Filling it in from the model's general knowledge of
    what an ``HW-3`` usually contains is precisely the failure NFR-2 exists to
    prevent, so the absence is persisted and flagged instead.

    Deliberately keyed to the *set*, not the opening: one definition serves every
    door that calls it. The opening-specific part — a rated door needs rated
    hardware — is applied at match time, where the opening is known (§5.8).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="hardware_components"
    )
    extraction_run = models.ForeignKey(
        ExtractionRun, on_delete=models.CASCADE, related_name="hardware_components"
    )

    hardware_group = models.CharField(
        max_length=100, db_index=True, help_text="The callout as written, e.g. 'HW-3'."
    )
    component_index = models.IntegerField(
        default=0, help_text="Order within the set, as the specification lists it."
    )

    resolved = models.BooleanField(
        default=False,
        help_text=(
            "False when the callout appears in the door schedule but its definition "
            "is not in this document. Never filled in from general knowledge (§5.11)."
        ),
    )
    explicit_part = models.BooleanField(
        default=False,
        help_text=(
            "The architect named a manufacturer part or series instead of a set. "
            "Not a resolution failure — the normal case (§1.3)."
        ),
    )

    # Raw strings, exactly as the specification wrote them. The model does not
    # normalise; §5.7 deterministic parsers and the matcher own interpretation.
    description = models.CharField(max_length=255, blank=True, default="")
    manufacturer = models.CharField(max_length=255, null=True, blank=True)
    part_number = models.CharField(max_length=255, null=True, blank=True)
    finish_raw = models.CharField(max_length=100, null=True, blank=True)
    quantity_raw = models.CharField(max_length=50, null=True, blank=True)
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Parsed from quantity_raw. Null when the source did not state one.",
    )

    review_state = models.CharField(
        max_length=50, choices=ReviewState.choices(), default=ReviewState.AUTO.value, db_index=True
    )
    review_notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["extraction_run", "hardware_group", "component_index"],
                name="uniq_hardware_component_per_run",
            ),
            # An unresolved callout has nothing to describe. Enforced in the
            # database because "we could not find this set" is the claim the
            # estimator acts on.
            models.CheckConstraint(
                condition=models.Q(resolved=True) | models.Q(description=""),
                name="ck_unresolved_component_has_no_description",
            ),
        ]
        indexes = [models.Index(fields=["project", "hardware_group"])]
        ordering = ["hardware_group", "component_index"]

    def __str__(self) -> str:
        if not self.resolved:
            return f"{self.hardware_group} (unresolved)"
        return f"{self.hardware_group}: {self.description}"


class FieldProvenance(TimestampedModel):
    """
    Field to source elements, with composite confidence (§7.2).

    ``page_number`` and the union bbox are denormalised onto this row on purpose:
    the openings grid is the primary screen, and joining
    ``field_provenance -> field_provenance_elements -> doc_elements`` for every
    field of every opening is bottleneck B12. The grid reads one table; only the
    detail view traverses the join.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    extraction_run = models.ForeignKey(
        ExtractionRun, on_delete=models.CASCADE, related_name="field_provenance"
    )
    opening = models.ForeignKey(
        Opening,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="provenance",
        help_text="Nullable so non-opening extractions reuse the same mechanism.",
    )
    hardware_component = models.ForeignKey(
        "HardwareSetComponent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="provenance",
        help_text=(
            "Set instead of ``opening`` for a hardware-set component (§5.11). Cross-"
            "schedule resolution runs through the same validation gate and the same "
            "provenance chain as opening extraction — there is only one contract."
        ),
    )
    field_name = models.CharField(max_length=100, db_index=True)
    extracted_value = models.TextField(
        null=True,
        blank=True,
        help_text="Null is a legitimate result — 'not present in the schedule' is a finding.",
    )

    # -- confidence components, stored individually (§5.9) --------------------
    # Every component is stored, not just the product, so a score can be
    # EXPLAINED rather than merely displayed.
    ocr_confidence = models.FloatField(
        null=True, blank=True, help_text="min across cited elements."
    )
    llm_confidence = models.FloatField(null=True, blank=True, help_text="Model self-report.")
    completeness_penalty = models.FloatField(
        default=1.0, help_text="f(fields_populated / fields_expected). Stored, not just applied."
    )
    final_confidence = models.FloatField(
        null=True, blank=True, help_text="min(ocr, llm) * penalty. Can never exceed either input."
    )
    grounding_score = models.FloatField(
        null=True, blank=True, help_text="Value-grounding similarity against the cited text (§5.6)."
    )

    # Denormalised for cheap grid reads (B12).
    page_number = models.IntegerField(null=True, blank=True)
    bbox_x_min = models.FloatField(null=True, blank=True)
    bbox_y_min = models.FloatField(null=True, blank=True)
    bbox_x_max = models.FloatField(null=True, blank=True)
    bbox_y_max = models.FloatField(null=True, blank=True)

    review_state = models.CharField(
        max_length=50, choices=ReviewState.choices(), default=ReviewState.AUTO.value, db_index=True
    )
    rejection_reason = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Why the §5.6 validation gate rejected the field. Rejected fields are "
            "flagged for review, never repaired and never silently dropped."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["extraction_run", "opening", "field_name"],
                name="uniq_provenance_per_field",
            )
        ]
        indexes = [
            models.Index(fields=["opening", "field_name"]),
            models.Index(fields=["extraction_run", "review_state"]),
            models.Index(fields=["review_state", "field_name"]),
        ]

    @property
    def cited_elements(self) -> list["DocElement"]:
        """
        The elements this field cites, in citation order.

        The detail view only. The grid must never call this — that traversal is
        precisely bottleneck B12, which the denormalised page_number and bbox on
        this row exist to avoid.
        """
        return [link.doc_element for link in self.elements.select_related("doc_element").all()]

    def __str__(self) -> str:
        return f"{self.field_name}={self.extracted_value!r}"


class FieldProvenanceElement(models.Model):
    """
    Join table: one field cites one or more ``doc_elements`` (§7.2, resolving C8).

    **Why a join table and not a uuid[] column.** Postgres supports array columns,
    but an array carries no referential integrity — and the entire contract is
    "if the model cannot point to a real element, the field is rejected." A real
    foreign key makes that contract enforced by the database, not by application
    code that might one day be bypassed. ``ON DELETE RESTRICT`` on the element side
    means an element can never be deleted out from under a live citation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    field_provenance = models.ForeignKey(
        FieldProvenance, on_delete=models.CASCADE, related_name="elements"
    )
    doc_element = models.ForeignKey(
        DocElement, on_delete=models.RESTRICT, related_name="citations"
    )
    ordinal = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["field_provenance", "doc_element"], name="uniq_citation"
            )
        ]
        indexes = [models.Index(fields=["field_provenance", "ordinal"])]
        ordering = ["field_provenance", "ordinal"]


class Match(TimestampedModel):
    """
    One ranked candidate for an opening (FR-4, §6.1, §7.4).

    Per-constraint verdicts are stored individually so a rejected match explains
    *which* constraint failed rather than merely scoring low. That mirrors the
    estimator behaviour CBC explicitly validated: "here are 3 close matches — is it
    one of these?"
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opening = models.ForeignKey(Opening, on_delete=models.CASCADE, related_name="matches")
    hardware_component = models.ForeignKey(
        HardwareSetComponent,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="matches",
        help_text=(
            "Null for the door itself; set for one component of the hardware set that "
            "opening calls for (§5.11). The opening is recorded either way, because "
            "the rating and handing hard constraints belong to the opening — the same "
            "HW-3 on a 90-minute door and on an unrated one are not the same match."
        ),
    )
    catalog_item = models.ForeignKey(
        "catalog.CatalogItem", on_delete=models.CASCADE, related_name="matches"
    )

    rank = models.IntegerField(help_text="1 = best.")
    match_confidence = models.FloatField()

    # -- hard constraints (§6.1). A false here is disqualifying regardless of score.
    rating_ok = models.BooleanField(
        help_text="A rated opening never matches an unrated item, regardless of text similarity."
    )
    handing_ok = models.BooleanField(help_text="An LH opening never matches an RH-only SKU.")
    division_ok = models.BooleanField(
        default=True, help_text="A Division 10 accessory never matches a Division 08 opening."
    )
    # -- scored constraints ---------------------------------------------------
    finish_ok = models.BooleanField()
    finish_score = models.FloatField(default=0.0)
    size_score = models.FloatField(default=0.0)
    vendor_score = models.FloatField(default=0.0)
    stock_score = models.FloatField(default=0.0)

    status = models.CharField(
        max_length=50,
        choices=MatchStatus.choices(),
        default=MatchStatus.PROPOSED.value,
        db_index=True,
    )
    is_direct_equal = models.BooleanField(
        default=False,
        help_text=(
            "The system RECORDS a substitution; it never decides one. Choosing an "
            "equal is estimator judgment (§1.4)."
        ),
    )
    substitution_note = models.TextField(blank=True)
    rejection_reason = models.TextField(
        blank=True, help_text="Which constraint failed, in words the estimator can act on."
    )

    class Meta:
        constraints = [
            # nulls_distinct=False so the door line (hardware_component IS NULL)
            # still collides with itself; without it Postgres treats every NULL as
            # unique and the constraint stops constraining the case it was written for.
            models.UniqueConstraint(
                fields=["opening", "hardware_component", "catalog_item"],
                name="uniq_match_pair",
                nulls_distinct=False,
            )
        ]
        indexes = [
            models.Index(fields=["opening", "rank"]),
            models.Index(fields=["opening", "status"]),
            models.Index(fields=["hardware_component", "rank"]),
        ]
        ordering = ["opening", "hardware_component", "rank"]

    def __str__(self) -> str:
        return f"#{self.rank} {self.catalog_item_id} ({self.match_confidence:.2f})"
