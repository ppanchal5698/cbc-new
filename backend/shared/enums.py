"""
Single definition of every enum used by BOTH services (§8.2).

Two services duplicating an enum is how ``READY_FOR_PROCESSING`` becomes ``READY``
in one of them. Django models build their ``choices`` from these; the FastAPI
pipeline imports the same objects. Nothing else may define these values.

Every member is a ``str`` subclass so ``.value`` round-trips through the database,
JSON, and SQS message bodies without conversion.
"""

from enum import StrEnum as _StrEnum


class StrEnum(_StrEnum):
    """
    Base for every enum here.

    Inherits the standard library's ``enum.StrEnum`` (Python 3.11+) rather than
    reimplementing ``str, Enum`` — it already compares equal to its own value and
    formats as that value. This class adds only the two Django helpers below.
    """

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Django ``choices=`` tuple list. Label is the member name."""
        return [(member.value, member.name) for member in cls]

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]


# ---------------------------------------------------------------------------
# Intake (§7.1, FR-1)
# ---------------------------------------------------------------------------

class SourceChannel(StrEnum):
    """How the bid request arrived. NR-5 covers the PHONE path."""

    EMAIL = "EMAIL"
    MANUAL = "MANUAL"
    PHONE = "PHONE"


class DocumentStatus(StrEnum):
    """
    Lifecycle of one uploaded PDF.

    ``READY_FOR_PROCESSING`` is the Django-to-worker handoff trigger (§3.2 rule 2):
    the ``post_save`` signal enqueues to SQS on transition INTO this state, and only
    this state. It is an enum rather than free text precisely because that contract
    spans two services.
    """

    UPLOADED = "UPLOADED"
    READY_FOR_PROCESSING = "READY_FOR_PROCESSING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class DocumentRole(StrEnum):
    """
    What a document is within its bid set.

    ADDENDUM is retained from the existing schema, but see Risk R2: it is *not* by
    itself an answer to FR-14. No reconciliation logic keys off it.
    """

    BID_SET = "BID_SET"
    ADDENDUM = "ADDENDUM"
    SPEC = "SPEC"
    RFP = "RFP"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# Preprocessing (§4.1)
# ---------------------------------------------------------------------------

class TextLayer(StrEnum):
    """
    Outcome of the per-page text-layer probe (§4.2).

    VECTOR_OUTLINED is the trap, and the reason this is four values rather than a
    boolean. Architectural PDFs frequently export text as vector outlines, so
    ``get_text()`` returns nothing and a naive probe concludes "scanned". OCR of a
    downsampled vector-outlined sheet loses the small annotation text where door
    numbers and ratings live, producing an empty extraction with *high* OCR
    confidence — the worst failure mode under NFR-2 (Risk R11). The detection rule
    lives in ``pipeline.stages.preprocess``.
    """

    RICH = "RICH"
    SPARSE = "SPARSE"
    NONE = "NONE"
    VECTOR_OUTLINED = "VECTOR_OUTLINED"


class PageClass(StrEnum):
    """What a page is. Drives ``OCRRoute`` through the routing table (§4.4)."""

    DOOR_SCHEDULE = "DOOR_SCHEDULE"
    HARDWARE_SCHEDULE = "HARDWARE_SCHEDULE"
    FRAME_SCHEDULE = "FRAME_SCHEDULE"
    FINISH_SCHEDULE = "FINISH_SCHEDULE"
    SPEC_TEXT = "SPEC_TEXT"
    DRAWING = "DRAWING"
    TITLE = "TITLE"
    INDEX = "INDEX"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def schedules(cls) -> frozenset["PageClass"]:
        """The classes whose cell/row/column structure is the whole point."""
        return frozenset(
            {cls.DOOR_SCHEDULE, cls.HARDWARE_SCHEDULE, cls.FRAME_SCHEDULE, cls.FINISH_SCHEDULE}
        )


class ClassMethod(StrEnum):
    """
    Which classification tier resolved a page (§4.3).

    Recorded per page so the expensive tier can be measured against the cheap ones.
    If MODEL is resolving pages KEYWORD should have caught, the anchor list needs
    work, not more Haiku calls.
    """

    BOOKMARK = "BOOKMARK"        # Tier 1 - PDF outline. Free, instant.
    TITLE_BLOCK = "TITLE_BLOCK"  # Tier 2 - sheet number in the corner region. Free.
    KEYWORD = "KEYWORD"          # Tier 3 - full-page anchors on native text. Free.
    MODEL = "MODEL"              # Tier 4 - Haiku on a low-DPI thumbnail. Paid.
    MANUAL = "MANUAL"            # Tier 5 - an estimator said so. Writes feedback.


class OCRRoute(StrEnum):
    """
    What to spend on a page (§4.4).

    Cost per 1,000 pages: TEXTRACT_TABLES $15 - TEXTRACT_TEXT $1.50 -
    NATIVE_TEXT $0 - SKIP $0. Getting this mapping right is bottleneck B1, the
    single highest-value change in the specification.
    """

    TEXTRACT_TABLES = "TEXTRACT_TABLES"
    TEXTRACT_TEXT = "TEXTRACT_TEXT"
    NATIVE_TEXT = "NATIVE_TEXT"
    SKIP = "SKIP"


class PageDiffStatus(StrEnum):
    """Per-page verdict when an addendum is compared to an earlier document (§4.7)."""

    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"


# ---------------------------------------------------------------------------
# Pipeline job tracking (§7.7)
# ---------------------------------------------------------------------------

class PipelineStage(StrEnum):
    """The seven stages, in execution order."""

    PREPROCESS = "PREPROCESS"
    OCR = "OCR"
    NORMALIZE = "NORMALIZE"
    EXTRACT = "EXTRACT"
    LINK = "LINK"
    MATCH = "MATCH"
    PRICE = "PRICE"

    @classmethod
    def order(cls) -> tuple["PipelineStage", ...]:
        return (
            cls.PREPROCESS, cls.OCR, cls.NORMALIZE, cls.EXTRACT,
            cls.LINK, cls.MATCH, cls.PRICE,
        )

    @property
    def index(self) -> int:
        return PipelineStage.order().index(self)


class PipelineJobStatus(StrEnum):
    """
    Status of one stage for one document.

    QUARANTINED is bottleneck B7: after ``maxReceiveCount: 3`` the message lands on
    the DLQ, and the job row must say so rather than sitting at FAILED indefinitely.
    A poison pill quarantines itself and pages someone instead of consuming the
    pipeline.
    """

    PENDING = "PENDING"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    SKIPPED = "SKIPPED"

    @classmethod
    def terminal(cls) -> frozenset["PipelineJobStatus"]:
        return frozenset({cls.COMPLETED, cls.QUARANTINED, cls.SKIPPED})


# ---------------------------------------------------------------------------
# Extraction and provenance (§7.2, §7.3)
# ---------------------------------------------------------------------------

class ElementType(StrEnum):
    """Kind of ``doc_elements`` row. Values are lowercase per §7.2."""

    WORD = "word"
    LINE = "line"
    TABLE_CELL = "table_cell"
    SELECTION_MARK = "selection_mark"


class ReviewState(StrEnum):
    """
    Drives FR-8 flagging and FR-9 approval.

    REJECTED means the §5.6 validation gate refused the field — a fabricated
    citation, or a value not grounded in the text it cited. Rejected fields are
    flagged for estimator review, never repaired and never silently dropped.
    """

    AUTO = "AUTO"
    FLAGGED = "FLAGGED"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class ExtractionRunStatus(StrEnum):
    """Status of one ``extraction_runs`` row."""

    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Handing(StrEnum):
    """
    Swing and hinge side relative to the viewing side (§1.3).

    Handed parts are separate SKUs, so this is a *hard* matching constraint
    (§6.1), never a scored one.
    """

    LH = "LH"
    RH = "RH"
    LHR = "LHR"
    RHR = "RHR"

    @property
    def is_reverse(self) -> bool:
        return self in (Handing.LHR, Handing.RHR)

    @property
    def side(self) -> str:
        """``L`` or ``R``. An LH opening never matches an RH-only SKU."""
        return self.value[0]


class FireRatingLocation(StrEnum):
    """
    Where a rating was found.

    CBC has not answered where fire ratings live on their bid sets (Open Item 9).
    Recording this per opening means the system accumulates the empirical answer
    across real bid sets instead of waiting for one (§5.8, Risk R1).
    """

    DOOR_SCHEDULE = "DOOR_SCHEDULE"
    FRAME_SCHEDULE = "FRAME_SCHEDULE"
    SPEC = "SPEC"
    MARGIN_NOTE = "MARGIN_NOTE"
    UNKNOWN = "UNKNOWN"


#: Valid fire ratings in minutes (UL 10C / NFPA 252), §1.3.
#: A parser that cannot land on one of these flags rather than guessing, and
#: NEVER defaults to unrated — an unrated door in a rated opening is a
#: code-compliance failure, not a cosmetic error.
FIRE_RATING_MINUTES: tuple[int, ...] = (20, 45, 60, 90)


# ---------------------------------------------------------------------------
# Matching (§7.4)
# ---------------------------------------------------------------------------

class MatchStatus(StrEnum):
    """
    Disposition of one proposed match.

    MANUAL and AWAITING_RFQ are the NR-13 long tail: below the confidence cut-off,
    or heavily customised, the estimator owns the line by design rather than by
    failure.
    """

    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    MANUAL = "MANUAL"
    AWAITING_RFQ = "AWAITING_RFQ"


# ---------------------------------------------------------------------------
# Catalogue and pricing (§7.5)
# ---------------------------------------------------------------------------

class ProductTypeBand(StrEnum):
    """Drives the margin band. Margins are data with effective dates, not constants."""

    COMMODITY = "COMMODITY"
    RESTROOM_PARTITIONS = "RESTROOM_PARTITIONS"
    SPECIALTY = "SPECIALTY"
    CUSTOM_FABRICATED = "CUSTOM_FABRICATED"


class CostSource(StrEnum):
    """
    The §6.2 cost waterfall, declared in strict priority order.

    MANUAL is last in priority but is a first-class path from day one, not a
    fallback (Risk R3): P21 item IDs diverge from manufacturer part numbers and
    semi-custom items will not match cleanly.
    """

    P21_LAST_PO = "P21_LAST_PO"
    DISTRIBUTOR_SHEET = "DISTRIBUTOR_SHEET"
    MFR_LIST = "MFR_LIST"
    VENDOR_RFQ = "VENDOR_RFQ"
    MANUAL = "MANUAL"

    @classmethod
    def waterfall(cls) -> tuple["CostSource", ...]:
        """Priority order for automatic sourcing. Declaration order IS the priority."""
        return (cls.P21_LAST_PO, cls.DISTRIBUTOR_SHEET, cls.MFR_LIST, cls.VENDOR_RFQ, cls.MANUAL)

    @property
    def priority(self) -> int:
        """Lower wins. Used to assert the waterfall honours its order."""
        return CostSource.waterfall().index(self)


class LineGroup(StrEnum):
    """
    FR-7 quote grouping.

    FREIGHT is a line with a nullable amount, never a computed number (C11). It
    renders ``TBD`` unless an estimator enters a value.
    """

    DOOR = "DOOR"
    RESTROOM_ACCESSORIES = "RESTROOM_ACCESSORIES"
    FREIGHT = "FREIGHT"
    OTHER = "OTHER"


class QuoteStatus(StrEnum):
    """
    Quote workflow.

    NFR-1 is a hard gate in this state machine: no export path exists without an
    APPROVED transition. The copilot drafts, sources, and calculates — it does not
    send.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    EXPORTED = "EXPORTED"


class VendorRFQStatus(StrEnum):
    """FR-16 vendor-RFQ loop."""

    REQUESTED = "REQUESTED"
    RECEIVED = "RECEIVED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Feedback (§7.5, FR-13)
# ---------------------------------------------------------------------------

class FeedbackEntity(StrEnum):
    """What a ``feedback`` row is about."""

    OPENING = "OPENING"
    FIELD_PROVENANCE = "FIELD_PROVENANCE"
    QUOTE_LINE = "QUOTE_LINE"
    MATCH = "MATCH"
    DOCUMENT_MANIFEST = "DOCUMENT_MANIFEST"


__all__ = [
    "StrEnum",
    "SourceChannel",
    "DocumentStatus",
    "DocumentRole",
    "TextLayer",
    "PageClass",
    "ClassMethod",
    "OCRRoute",
    "PageDiffStatus",
    "PipelineStage",
    "PipelineJobStatus",
    "ElementType",
    "ReviewState",
    "ExtractionRunStatus",
    "Handing",
    "FireRatingLocation",
    "FIRE_RATING_MINUTES",
    "MatchStatus",
    "ProductTypeBand",
    "CostSource",
    "LineGroup",
    "QuoteStatus",
    "VendorRFQStatus",
    "FeedbackEntity",
]
