"""
The bid board's derived Status column.

Status is not stored (see :mod:`projects.board`), so the thing worth testing is
the precedence: a bid can be several of these at once — flagged fields *and* a
running job *and* an open vendor RFQ — and the board has to say the one that
matters most. The order is the member order of :class:`shared.enums.BoardStatus`.
"""

import pytest
from factories import (
    DocumentFactory,
    FieldProvenanceFactory,
    OpeningFactory,
    PipelineJobFactory,
    ProjectFactory,
    QuoteFactory,
    QuoteLineFactory,
    VendorRFQFactory,
)

from projects.board import BOARD_FILTERS, annotate_board, board_totals
from projects.models import Project
from shared.enums import BoardStatus

pytestmark = pytest.mark.django_db


def status_of(project) -> str:
    return annotate_board(Project.objects.filter(pk=project.pk)).first().board_status


def test_a_bid_with_nothing_on_it_is_intake():
    assert status_of(ProjectFactory()) == BoardStatus.INTAKE.value


def test_a_running_pipeline_job_reads_as_extracting():
    project = ProjectFactory()
    PipelineJobFactory(document=DocumentFactory(project=project), project=project, status="STARTED")
    assert status_of(project) == BoardStatus.EXTRACTING.value


def test_a_finished_job_does_not_leave_the_bid_extracting():
    project = ProjectFactory()
    PipelineJobFactory(
        document=DocumentFactory(project=project), project=project, status="COMPLETED"
    )
    assert status_of(project) == BoardStatus.INTAKE.value


def test_a_flagged_field_reads_as_review():
    project = ProjectFactory()
    FieldProvenanceFactory(opening=OpeningFactory(project=project), review_state="FLAGGED")
    assert status_of(project) == BoardStatus.REVIEW.value


def test_a_draft_quote_with_nothing_flagged_reads_as_in_progress():
    project = ProjectFactory()
    QuoteFactory(project=project, status="DRAFT")
    assert status_of(project) == BoardStatus.IN_PROGRESS.value


def test_an_exported_quote_reads_as_sent():
    project = ProjectFactory()
    QuoteFactory(project=project, status="EXPORTED")
    assert status_of(project) == BoardStatus.SENT.value


def test_an_open_vendor_rfq_outranks_extraction_and_review():
    """The estimator is waiting on someone else — that is the actionable fact."""
    project = ProjectFactory()
    quote = QuoteFactory(project=project)
    VendorRFQFactory(quote_line=QuoteLineFactory(quote=quote), status="REQUESTED")
    PipelineJobFactory(document=DocumentFactory(project=project), project=project, status="STARTED")
    FieldProvenanceFactory(opening=OpeningFactory(project=project), review_state="FLAGGED")
    assert status_of(project) == BoardStatus.AWAITING_VENDOR.value


def test_the_outcome_beats_every_derived_state():
    """Won is a human's answer. Nothing the pipeline does afterwards overrides it."""
    project = ProjectFactory(outcome="WON")
    PipelineJobFactory(document=DocumentFactory(project=project), project=project, status="STARTED")
    assert status_of(project) == BoardStatus.WON.value


def test_flag_count_counts_each_flag_once_per_bid():
    """
    The count is a subquery per source rather than a join, because joining
    documents, openings and quote lines multiplies the rows and then counts the
    multiplication — three documents would report three times the flags.
    """
    project = ProjectFactory()
    DocumentFactory(project=project)
    DocumentFactory(project=project)
    DocumentFactory(project=project)
    opening = OpeningFactory(project=project)
    FieldProvenanceFactory(opening=opening, field_name="fire_rating", review_state="FLAGGED")
    FieldProvenanceFactory(opening=opening, field_name="handing", review_state="REJECTED")
    QuoteLineFactory(quote=QuoteFactory(project=project), needs_review=True)

    row = annotate_board(Project.objects.filter(pk=project.pk)).first()
    assert row.flag_count == 3


def test_version_label_names_the_addendum():
    project = ProjectFactory()
    DocumentFactory(project=project, role="BID_SET")
    assert annotate_board(Project.objects.filter(pk=project.pk)).first().version_label == "Base bid"

    DocumentFactory(project=project, role="ADDENDUM")
    assert annotate_board(Project.objects.filter(pk=project.pk)).first().version_label == "Addendum 1"


def test_quoted_value_is_the_newest_quote_not_the_sum_of_revisions():
    project = ProjectFactory()
    QuoteFactory(project=project, grand_total="1000.00")
    QuoteFactory(project=project, grand_total="1250.00")

    row = annotate_board(Project.objects.filter(pk=project.pk)).first()
    assert str(row.quoted_value) == "1250.00"
    assert board_totals(annotate_board(Project.objects.filter(pk=project.pk)))["value"] == row.quoted_value


def test_in_flight_excludes_sent_and_closed():
    live = ProjectFactory()
    sent = ProjectFactory()
    QuoteFactory(project=sent, status="EXPORTED")
    closed = ProjectFactory(outcome="LOST")

    in_flight = annotate_board(Project.objects.all()).filter(BOARD_FILTERS["In flight"])
    assert set(in_flight.values_list("id", flat=True)) == {live.id}
    assert set(
        annotate_board(Project.objects.all()).filter(BOARD_FILTERS["Closed"]).values_list("id", flat=True)
    ) == {closed.id}
    assert set(
        annotate_board(Project.objects.all()).filter(BOARD_FILTERS["Sent"]).values_list("id", flat=True)
    ) == {sent.id}
