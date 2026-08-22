"""
The bid board's derived columns.

The board shows a Status, a quoted Value, a Version and a flag count per bid. Only
two of those facts are the project's own (``due_date`` and ``outcome``); the rest
the system already knows from the pipeline, the review flags and the quote. So
they are **derived here in one query set** rather than stored on the row.

That is the design decision worth reading this file for: a stored ``status``
column would have to be written by whoever happened to notice the worker finish,
and would be wrong the moment nobody did. A derived one cannot disagree with the
pipeline because it *is* the pipeline.

Every derivation is a correlated subquery rather than a join. Joining openings,
provenance, quotes and lines into one row set multiplies the rows and then counts
the multiplication -- which is how a bid with three documents reports nine flags.
"""

from django.db.models import (
    Case,
    CharField,
    Count,
    DecimalField,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Concat

from shared.enums import (
    BidOutcome,
    BoardStatus,
    DocumentRole,
    PipelineJobStatus,
    QuoteStatus,
    ReviewState,
    VendorRFQStatus,
)

#: A pipeline job in none of these states is still working.
_FINISHED = [
    PipelineJobStatus.COMPLETED.value,
    PipelineJobStatus.FAILED.value,
    PipelineJobStatus.QUARANTINED.value,
    PipelineJobStatus.SKIPPED.value,
]

_NEEDS_REVIEW = [ReviewState.FLAGGED.value, ReviewState.REJECTED.value]


def _count(queryset, group: str):
    """
    A correlated COUNT that returns 0 rather than NULL for an empty set.

    ``group`` is the column the subquery is correlated on. It has to be named:
    grouping by the row's own pk would count one per row instead of one per bid.
    """
    return Coalesce(
        Subquery(
            queryset.order_by().values(group).annotate(n=Count("*")).values("n")[:1],
            output_field=IntegerField(),
        ),
        Value(0),
    )


def annotate_board(queryset: QuerySet) -> QuerySet:
    """Add ``board_status``, ``quoted_value``, ``flag_count`` and ``version_label``."""
    from openings.models import FieldProvenance
    from quotes.models import Quote, QuoteLine, VendorRFQ

    from .models import Document, PipelineJob

    project = OuterRef("pk")

    running = PipelineJob.objects.filter(project=project).exclude(status__in=_FINISHED)
    awaiting = VendorRFQ.objects.filter(
        quote_line__quote__project=project, status=VendorRFQStatus.REQUESTED.value
    )
    exported = Quote.objects.filter(project=project, status=QuoteStatus.EXPORTED.value)
    any_quote = Quote.objects.filter(project=project)
    flagged_fields = FieldProvenance.objects.filter(
        opening__project=project, review_state__in=_NEEDS_REVIEW
    )
    flagged_lines = QuoteLine.objects.filter(quote__project=project, needs_review=True)
    addenda = Document.objects.filter(project=project, role=DocumentRole.ADDENDUM.value)

    return (
        queryset.annotate(
            _running=Exists(running),
            _awaiting=Exists(awaiting),
            _exported=Exists(exported),
            _quoted=Exists(any_quote),
            _flagged=Exists(flagged_fields) | Exists(flagged_lines),
            _addenda=_count(addenda, "project"),
            # The newest quote's total, not the sum of every revision: a bid that
            # has been re-quoted twice is worth one number, not three.
            quoted_value=Coalesce(
                Subquery(
                    Quote.objects.filter(project=project)
                    .order_by("-created_at")
                    .values("grand_total")[:1],
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
                Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            ),
            flag_count=_count(flagged_fields, "opening__project")
            + _count(flagged_lines, "quote__project"),
        )
        .annotate(
            # First branch that holds wins, in BoardStatus member order.
            board_status=Case(
                When(outcome=BidOutcome.WON.value, then=Value(BoardStatus.WON.value)),
                When(outcome=BidOutcome.LOST.value, then=Value(BoardStatus.LOST.value)),
                When(_exported=True, then=Value(BoardStatus.SENT.value)),
                When(_awaiting=True, then=Value(BoardStatus.AWAITING_VENDOR.value)),
                When(_running=True, then=Value(BoardStatus.EXTRACTING.value)),
                When(_flagged=True, then=Value(BoardStatus.REVIEW.value)),
                When(_quoted=True, then=Value(BoardStatus.IN_PROGRESS.value)),
                default=Value(BoardStatus.INTAKE.value),
            ),
            version_label=Case(
                When(_addenda=0, then=Value("Base bid")),
                default=Concat(Value("Addendum "), Cast("_addenda", CharField())),
            ),
        )
    )


#: The board's filter chips. Each is a Q against the annotations above.
BOARD_FILTERS = {
    "All": Q(),
    "In flight": ~Q(board_status__in=[BoardStatus.SENT.value, BoardStatus.WON.value, BoardStatus.LOST.value]),
    "Sent": Q(board_status=BoardStatus.SENT.value),
    "Closed": Q(board_status__in=[BoardStatus.WON.value, BoardStatus.LOST.value]),
}


def board_totals(queryset: QuerySet) -> dict:
    """The header line: ``13 jobs · $463,250 quoted value``."""
    agg = queryset.aggregate(jobs=Count("id", distinct=True), value=Sum("quoted_value"))
    return {"jobs": agg["jobs"] or 0, "value": agg["value"] or 0}
