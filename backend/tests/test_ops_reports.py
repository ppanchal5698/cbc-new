"""
The operator reports, checked against real rows (§5.9, §10.3).

Both scripts previously printed hardcoded numbers. The point of this file is that
they now cannot: the arithmetic below runs against factory-built database rows,
so a report that stops reflecting the tables fails here rather than in a meeting
where someone is reading a dollar figure aloud.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

OPS_SCRIPTS = Path(__file__).resolve().parents[2] / "ops" / "scripts"
if not OPS_SCRIPTS.exists():
    # Container layout: ops/ is mounted inside the backend root.
    OPS_SCRIPTS = Path(__file__).resolve().parents[1] / "ops" / "scripts"
if str(OPS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(OPS_SCRIPTS))

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# calibrate_threshold — the curve arithmetic
# ---------------------------------------------------------------------------

def _curve():
    from calibrate_threshold import curve

    return curve


def test_a_low_threshold_flags_nothing_and_lets_every_error_escape():
    """The degenerate end of the curve, which is where 'why flag anything' lives."""
    samples = [(0.95, False), (0.90, True), (0.85, True), (0.99, False)]
    row = next(r for r in _curve()(samples) if r["threshold"] == 0.50)

    assert row["flag_rate"] == 0.0
    assert row["escape_rate"] == 1.0
    assert row["escaped"] == 2


def test_a_high_threshold_flags_almost_everything_and_lets_nothing_escape():
    """
    The other end: near-total catch, near-zero throughput benefit. The real trade.

    The threshold is exclusive — a field is flagged when ``confidence <
    threshold`` — so a value sitting exactly on 0.99 stays unflagged even at the
    top of the sweep. That boundary is asserted rather than glossed over, because
    an off-by-one here silently shifts every published flag rate.
    """
    samples = [(0.95, False), (0.90, True), (0.85, True), (0.99, False)]
    row = next(r for r in _curve()(samples) if r["threshold"] == 0.99)

    assert row["flagged"] == 3
    assert row["flag_rate"] == 0.75
    assert row["escape_rate"] == 0.0


def test_escape_rate_counts_only_unflagged_errors():
    """
    An error the system flagged is the system working, and must not be counted
    against it. Getting this backwards would make a well-calibrated threshold
    look like a broken one.
    """
    # 0.70 is wrong but will be flagged at 0.80; 0.95 is wrong and will not be.
    samples = [(0.70, True), (0.95, True), (0.99, False)]
    row = next(r for r in _curve()(samples) if r["threshold"] == 0.80)

    assert row["flagged"] == 1
    assert row["escaped"] == 1
    assert row["escape_rate"] == 0.5


def test_recommend_picks_the_cheapest_threshold_that_meets_the_target():
    from calibrate_threshold import recommend

    samples = [(0.60, True), (0.75, True), (0.95, False), (0.99, False)]
    rows = _curve()(samples)

    pick = recommend(rows, max_escape_rate=0.0)
    assert pick is not None
    # Must catch both errors (so > 0.75) while flagging as little as possible.
    assert pick["threshold"] == pytest.approx(0.76)
    assert pick["escape_rate"] == 0.0


def test_recommend_returns_none_when_no_threshold_can_hit_the_target():
    """
    A field the model is simply bad at has no viable operating point, and the
    honest output is "no threshold works", not the closest one.
    """
    from calibrate_threshold import recommend

    # Every error is high-confidence: confidently wrong is the one thing a
    # threshold cannot fix.
    samples = [(0.99, True), (0.99, True), (0.99, False)]
    assert recommend(_curve()(samples), max_escape_rate=0.10) is None


def test_gather_samples_treats_an_estimator_correction_as_ground_truth(django_user_model):
    """A feedback row against a provenance is what 'this value was wrong' means."""
    from calibrate_threshold import gather_samples
    from factories import FeedbackFactory, FieldProvenanceFactory

    from shared.enums import FeedbackEntity

    wrong = FieldProvenanceFactory(field_name="handing", final_confidence=0.91)
    right = FieldProvenanceFactory(field_name="handing", final_confidence=0.93)
    FeedbackFactory(
        entity_type=FeedbackEntity.OPENING.value,
        entity_id=wrong.opening_id,
        field_name="handing",
        field_provenance=wrong,
    )

    samples = gather_samples()["handing"]
    assert sorted(samples) == [(0.91, True), (0.93, False)]
    assert right.id  # the uncorrected one is present and marked correct


def test_gather_samples_counts_a_rejected_citation_as_wrong():
    """
    §5.6 rejection is the other ground truth: the gate already established the
    value was not supported by what it cited.
    """
    from calibrate_threshold import gather_samples
    from factories import FieldProvenanceFactory

    from shared.enums import ReviewState

    FieldProvenanceFactory(
        field_name="fire_rating_raw",
        final_confidence=0.88,
        review_state=ReviewState.REJECTED.value,
    )
    assert gather_samples()["fire_rating_raw"] == [(0.88, True)]


# ---------------------------------------------------------------------------
# cost_report — attribution against real rows
# ---------------------------------------------------------------------------

def test_document_costs_sums_manifest_rows_by_route():
    from cost_report import document_costs
    from factories import DocumentFactory, DocumentManifestFactory

    document = DocumentFactory()
    for page in range(1, 4):
        DocumentManifestFactory(
            document=document,
            page_number=page,
            ocr_route="TEXTRACT_TABLES",
            ocr_cost_estimate=Decimal("0.015"),
        )
    for page in range(4, 14):
        DocumentManifestFactory(
            document=document,
            page_number=page,
            page_class="DRAWING",
            ocr_route="SKIP",
            ocr_cost_estimate=Decimal("0"),
        )

    report = document_costs(document)

    assert report["page_count"] == 13
    assert report["by_route"]["SKIP"]["pages"] == 10
    assert report["ocr_cost"] == Decimal("0.045")
    # The whole argument for §4: 13 pages read naively would be $0.195.
    assert report["ocr_cost"] < Decimal("13") * Decimal("0.015")


def test_document_costs_includes_bedrock_spend_and_tokens():
    from cost_report import document_costs
    from factories import DocumentFactory, ExtractionRunFactory

    document = DocumentFactory()
    ExtractionRunFactory(
        document=document,
        input_tokens=40_000,
        cached_input_tokens=32_000,
        output_tokens=2_000,
        cost_usd=Decimal("0.0912"),
    )

    report = document_costs(document)

    assert report["llm_cost"] == Decimal("0.0912")
    assert report["cached_input_tokens"] == 32_000
    assert report["total"] == Decimal("0.0912")


def test_document_costs_surfaces_retried_jobs():
    """
    A redelivered job is potential double spend. It has to be visible in the
    report, because the guard that prevents it is exactly the thing that can
    silently stop working.
    """
    from cost_report import document_costs
    from factories import DocumentFactory, PipelineJobFactory

    document = DocumentFactory()
    PipelineJobFactory(document=document, stage="OCR", attempt=3)
    PipelineJobFactory(document=document, stage="PREPROCESS", attempt=1)

    retried = document_costs(document)["retried_jobs"]

    assert [job.stage for job in retried] == ["OCR"]


def test_document_costs_prefers_actual_cost_over_estimate():
    from cost_report import document_costs
    from factories import DocumentFactory, PipelineJobFactory

    document = DocumentFactory()
    PipelineJobFactory(
        document=document, stage="OCR", cost_estimate=Decimal("1.00"), cost_actual=Decimal("0.25")
    )

    assert document_costs(document)["job_cost"] == Decimal("0.25")
