"""
Projects, documents, the preprocessing manifest, and job tracking (§7.1, §7.2, §7.7).

A **Project** is the bid. A **Document** is one PDF within it — a bid set arrives as
one combined PDF or several separate ones (§1.2), so the two are not the same thing
and provenance keys to the document, not the bid.
"""

import uuid

from django.conf import settings
from django.db import models

from shared.enums import (
    ClassMethod,
    DocumentRole,
    DocumentStatus,
    OCRRoute,
    PageClass,
    PageDiffStatus,
    PipelineJobStatus,
    PipelineStage,
    SourceChannel,
    TextLayer,
)


class TimestampedModel(models.Model):
    """``created_at``/``updated_at`` on every table, per §7."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimestampedModel):
    """One bid. FR-1 intake fields plus the FR-11 prior-quote lookup keys."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)

    source_channel = models.CharField(
        max_length=50,
        choices=SourceChannel.choices(),
        default=SourceChannel.MANUAL.value,
        help_text="How the bid arrived. PHONE covers the NR-5 'create new bid request' path.",
    )
    # FR-10: the quote routes back to whoever initiated the request
    # (Kellan/Matt/Rebecca/Tina), never a group inbox.
    initiator_email = models.EmailField(max_length=255)
    initiator_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_projects",
        help_text="Set when the initiator is a known internal user.",
    )
    rfp_body_text = models.TextField(
        blank=True, help_text="Unstructured RFP context from the intake email."
    )

    # FR-11 prior-quote reuse keys. Nullable: a phone-in bid may have none of them.
    brand = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    architect = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    general_contractor = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["brand", "architect"]),
            models.Index(fields=["general_contractor"]),
        ]

    def __str__(self) -> str:
        return self.name


class Document(TimestampedModel):
    """
    One PDF within a bid set.

    ``file_key`` is written once by the verified intake path and never mutated: the
    source bucket is versioned with Object Lock in GOVERNANCE mode (§11.3, B17).
    Every preprocessing output lands in the derived bucket instead.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")

    filename = models.CharField(max_length=255)
    role = models.CharField(
        max_length=50,
        choices=DocumentRole.choices(),
        default=DocumentRole.BID_SET.value,
        help_text=(
            "ADDENDUM is retained but is NOT by itself an answer to FR-14 (Risk R2): "
            "CBC has not said whether an addendum is a new document, a revision, or both. "
            "No reconciliation logic keys off this field."
        ),
    )

    # -- immutable source pointer (§3.3 step 2) --------------------------------
    file_key = models.CharField(
        max_length=1024, help_text="S3 key in the source bucket. Built by shared.s3_keys only."
    )
    file_version_id = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
        help_text="S3 object version-ID captured at upload; part of the OCR idempotency key (B8).",
    )
    checksum_sha256 = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Computed at upload and matched against S3's own checksum.",
    )
    size_bytes = models.BigIntegerField(null=True, blank=True)
    #: Increments when the same logical document is re-uploaded. Distinct from the
    #: S3 version-ID: this is what appears in derived-bucket key templates.
    version = models.IntegerField(default=1)

    status = models.CharField(
        max_length=50,
        choices=DocumentStatus.choices(),
        default=DocumentStatus.UPLOADED.value,
        db_index=True,
        help_text=(
            "Transition INTO READY_FOR_PROCESSING is the Django-to-worker handoff "
            "trigger (§3.2 rule 2) — the post_save signal enqueues to SQS on that "
            "transition and no other."
        ),
    )
    status_detail = models.TextField(
        blank=True, help_text="Why the document failed or was quarantined. Shown to the estimator."
    )

    # -- OCR artefacts (§7.1) --------------------------------------------------
    ocr_result_key = models.CharField(
        max_length=1024, null=True, blank=True, help_text="Persisted raw OCR JSON, gzipped."
    )
    ocr_result_version_id = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
        help_text="S3 version, so a re-run is distinguishable from an overwrite.",
    )

    # -- preprocessing outcome -------------------------------------------------
    page_count = models.IntegerField(null=True, blank=True, help_text="From the manifest.")
    manifest_complete = models.BooleanField(
        default=False,
        help_text=(
            "Preprocessing finished before OCR was attempted. §4.1 makes this an "
            "invariant, not a convenience: the manifest is the audit answer to "
            "'why didn't the system read page 47?'"
        ),
    )
    is_encrypted = models.BooleanField(default=False)
    was_repaired = models.BooleanField(
        default=False,
        help_text="pikepdf repaired a structurally damaged file into derived; source untouched (§4.2).",
    )
    split_part_count = models.IntegerField(
        default=1, help_text="Parts the document was split into for Textract's limits (§4.6)."
    )
    estimated_ocr_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Logged BEFORE the spend, so MAX_OCR_COST_PER_DOCUMENT_USD can veto it (§10.3).",
    )

    class Meta:
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "filename", "version"], name="uniq_document_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.filename} (v{self.version})"


class DocumentManifest(TimestampedModel):
    """
    One row per page, persisted BEFORE the first OCR call (§4.1).

    This is not bookkeeping. It is the audit answer to "why didn't the system read
    page 47?", which NFR-3 will eventually require someone to answer, and it is what
    makes every ``SKIP`` visible in the review UI with a reason (Risk R12).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="manifest_pages"
    )
    page_number = models.IntegerField(
        help_text="Document-global, 1-based. Split parts are offset back before writing (§4.6)."
    )

    page_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text="sha256 of the normalised page content stream; drives addendum diffing (§4.7).",
    )
    width_pt = models.FloatField(null=True, blank=True)
    height_pt = models.FloatField(null=True, blank=True)
    rotation = models.IntegerField(
        default=0,
        help_text=(
            "Applied at raster time. A rotated sheet rendered without it produces "
            "polygons that overlay 90 degrees off — the single most common cause of "
            "'the highlight is in the wrong place' (§4.5)."
        ),
    )

    # -- text-layer probe (§4.2) ----------------------------------------------
    text_layer = models.CharField(
        max_length=50, choices=TextLayer.choices(), default=TextLayer.NONE.value
    )
    native_word_count = models.IntegerField(default=0)
    vector_path_count = models.IntegerField(default=0)

    # -- classification (§4.3) -------------------------------------------------
    page_class = models.CharField(
        max_length=50, choices=PageClass.choices(), default=PageClass.UNKNOWN.value, db_index=True
    )
    class_confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    class_method = models.CharField(
        max_length=50,
        choices=ClassMethod.choices(),
        null=True,
        blank=True,
        help_text="Which tier resolved this page — lets the expensive tier be measured.",
    )

    # -- routing (§4.4) --------------------------------------------------------
    ocr_route = models.CharField(
        max_length=50, choices=OCRRoute.choices(), default=OCRRoute.SKIP.value, db_index=True
    )
    route_reason = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Human-readable justification, always surfaced for SKIP. Design rule: "
            "never silently skip (§4.3)."
        ),
    )
    forced_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forced_page_reads",
        help_text="An estimator overrode the routing decision — 'read page 47 anyway' (Risk R12).",
    )

    raster_key = models.CharField(max_length=1024, null=True, blank=True)
    thumb_key = models.CharField(max_length=1024, null=True, blank=True)
    ocr_input_key = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
        help_text="300 DPI render, only for VECTOR_OUTLINED pages routed to OCR (Risk R11).",
    )
    ocr_cost_estimate = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    #: Offset applied to convert a part-local page number back to document-global.
    page_offset = models.IntegerField(default=0)
    split_part = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "page_number"], name="uniq_manifest_page"
            )
        ]
        indexes = [
            models.Index(fields=["document", "ocr_route"]),
            models.Index(fields=["document", "page_class"]),
        ]
        ordering = ["document", "page_number"]

    def __str__(self) -> str:
        return f"p{self.page_number} {self.page_class} -> {self.ocr_route}"


class PipelineJob(TimestampedModel):
    """
    The shared handoff record between Django and the FastAPI worker (§7.7).

    Django enqueues and reads status from this table; the worker advances it
    through stages. No synchronous cross-service dependency, and a worker restart
    loses nothing (§3.2 rule 2).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="pipeline_jobs", null=True, blank=True
    )
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="pipeline_jobs"
    )
    stage = models.CharField(max_length=50, choices=PipelineStage.choices())
    status = models.CharField(
        max_length=50,
        choices=PipelineJobStatus.choices(),
        default=PipelineJobStatus.PENDING.value,
        db_index=True,
    )
    attempt = models.IntegerField(default=0, help_text="Incremented on every delivery.")

    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        help_text=(
            "sha256(document_version_id + feature_set + route_config_version). SQS is "
            "at-least-once and a slow job WILL be redelivered; without this a retry "
            "storm double-bills Textract (bottleneck B8)."
        ),
    )
    external_job_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Textract JobId. Written BEFORE the call is considered complete, so a "
            "redelivery resumes from the existing job instead of submitting a second "
            "one (B8)."
        ),
    )

    error_detail = models.TextField(blank=True)

    cost_estimate = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    cost_actual = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Cost per bid set is a first-class metric — see 'make cost-report' (§10.3).",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "stage"], name="uniq_job_document_stage")
        ]
        indexes = [
            models.Index(fields=["document", "stage", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.stage}/{self.status} {self.document_id}"


class BidAlternate(TimestampedModel):
    """
    Base bid vs Alternate 1/2 (FR-14, §7.6).

    Built so base-bid and alternate totals present as separate comparable figures.
    **No reconciliation logic and no UI until Open Item 11 is answered** (Risk R2) —
    CBC has not said how alternates are quoted or how addenda are reconciled, and
    guessing produces a silently wrong comparison.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="bid_alternates")
    designation = models.CharField(max_length=100, help_text="'BASE', 'Alternate 1', ...")
    description = models.TextField(blank=True)
    source_document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="bid_alternates"
    )
    is_base_bid = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "designation"], name="uniq_alternate_designation"
            )
        ]

    def __str__(self) -> str:
        return self.designation


class PageDiff(TimestampedModel):
    """
    Per-page addendum diff (§4.7, B13).

    Turns "an addendum arrived" from a full reprocess into a diff: unchanged pages
    reuse their existing elements and extractions at zero OCR and zero LLM cost.
    The diff report is safe to build now; reconciliation is not (Risk R2).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="page_diffs")
    compared_to_document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="page_diffs_against",
        help_text="The earlier document being compared against.",
    )
    page_number = models.IntegerField()
    status = models.CharField(max_length=50, choices=PageDiffStatus.choices())
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "compared_to_document", "page_number"], name="uniq_page_diff"
            )
        ]
        indexes = [models.Index(fields=["document", "status"])]

    def __str__(self) -> str:
        return f"p{self.page_number}: {self.status}"
