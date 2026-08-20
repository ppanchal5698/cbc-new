"""
Metrics and tracing (§11.5).

Two things worth a test here, and they are both about failure modes that are
invisible in production.

A malformed EMF record does not error — CloudWatch just quietly declines to
create the metric, so the dashboard stays empty and looks like "nothing
happened". The shape assertions below are the only place that gets caught.

And tracing must never be able to break a stage. `trace_segment` runs with no
X-Ray daemon in every local run and every test, so the no-daemon path is the
common path, not the edge case.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from pipeline.observability import metrics
from pipeline.observability.logging_setup import job_context, timed


def _records(capsys) -> list[dict]:
    """EMF records printed to stdout, ignoring ordinary log lines."""
    out = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if "_aws" in payload:
            out.append(payload)
    return out


def test_emit_produces_a_valid_emf_record(capsys):
    metrics.emit({"PagesReceived": 65, "OCRCostUSD": Decimal("0.12")}, stage="preprocess")

    (record,) = _records(capsys)
    directive = record["_aws"]["CloudWatchMetrics"][0]

    assert directive["Namespace"] == metrics.NAMESPACE
    assert directive["Dimensions"] == [["stage"]]
    assert {m["Name"] for m in directive["Metrics"]} == {"PagesReceived", "OCRCostUSD"}
    assert record["stage"] == "preprocess"
    # Decimal is not JSON-serialisable; the emitter must coerce or the whole
    # record is lost at the point of writing it.
    assert record["OCRCostUSD"] == 0.12


def test_every_named_metric_declares_a_unit():
    """
    A metric without a unit renders as "Count" forever, including dollar amounts.
    Cheap to assert, annoying to discover on a dashboard six months later.
    """
    metrics.emit({"StageDurationMs": 1})
    for name in metrics.UNITS.values():
        assert name in {"None", "Count", "Percent", "Milliseconds", "Seconds"}


def test_emit_puts_correlation_ids_in_the_body_not_the_dimensions(capsys):
    """
    A document_id dimension would mint one CloudWatch metric per bid set —
    expensive, and unalarmed, since you cannot alarm on a metric that exists once.
    """
    with job_context(pipeline_job_id="job-1", doc_id="doc-1", stage="extract"):
        metrics.emit({"OpeningsExtracted": 4}, stage="extract")

    (record,) = _records(capsys)
    assert record["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["stage"]]
    assert record["pipeline_job_id"] == "job-1"
    assert record["document_id"] == "doc-1"


def test_emit_ignores_an_empty_metric_set(capsys):
    """A record with no metrics is noise in the log stream and nothing else."""
    metrics.emit({})
    assert _records(capsys) == []


def test_citation_rejection_rate_is_derived_not_passed_in(capsys):
    """The first headline signal (§5.6). Computed once, so it cannot disagree."""
    metrics.record_extraction(
        openings=4,
        fields_emitted=40,
        fields_rejected_citation=3,
        fields_rejected_grounding=1,
        fields_flagged=6,
        input_tokens=1000,
        cached_input_tokens=800,
        output_tokens=200,
        cost=Decimal("0.02"),
    )

    (record,) = _records(capsys)
    assert record["CitationRejectionRate"] == pytest.approx(10.0)


def test_extraction_metrics_omit_the_rate_when_nothing_was_emitted(capsys):
    """Zero fields is not a zero rejection rate; it is no measurement at all."""
    metrics.record_extraction(
        openings=0,
        fields_emitted=0,
        fields_rejected_citation=0,
        fields_rejected_grounding=0,
        fields_flagged=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        cost=None,
    )

    (record,) = _records(capsys)
    assert "CitationRejectionRate" not in record
    assert "BedrockCostUSD" not in record


def test_trace_segment_is_inert_without_a_daemon():
    """Tracing must never be able to fail the thing it is observing."""
    with metrics.trace_segment("preprocess") as segment:
        assert segment is None


def test_trace_segment_does_not_swallow_the_stage_error():
    """Inert is not the same as silent: the real exception still propagates."""
    with pytest.raises(ValueError, match="boom"), metrics.trace_segment("extract"):
        raise ValueError("boom")


def test_timed_emits_stage_duration_on_success(capsys):
    with timed("normalize"):
        pass

    (record,) = _records(capsys)
    assert record["outcome"] == "ok"
    assert record["stage"] == "normalize"
    assert record["StageDurationMs"] >= 0


def test_timed_emits_stage_duration_on_failure(capsys):
    """
    The failing case is the one worth measuring: a stage that dies after four
    minutes and reports nothing is indistinguishable from a stage that never ran.
    """
    with pytest.raises(RuntimeError), timed("ocr"):
        raise RuntimeError("textract said no")

    (record,) = _records(capsys)
    assert record["outcome"] == "failed"
    assert record["stage"] == "ocr"
