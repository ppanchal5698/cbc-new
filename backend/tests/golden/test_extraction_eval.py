"""
The eval gate, as a test (§5.10).

`make eval` runs the harness for a human to read. This runs the same measurement
for CI to fail on. It is marked ``integration`` because it needs the golden bid
set and, for the extraction half, a database with a completed run.

The file it replaces asserted a score it had just hardcoded. If this one ever
starts passing without measuring anything, that is the bug — hence
``test_gate_refuses_to_pass_without_a_baseline`` below.
"""

from __future__ import annotations

import pytest

from tests.golden import run_eval

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def results(django_db_setup, django_db_blocker):
    """
    One measurement shared by every test here — the classifier half reads all 65
    pages of a 24 MB PDF and takes the better part of a minute.

    ``django_db_blocker.unblock`` rather than the ``django_db`` mark because that
    mark is function-scoped, and re-triaging the bid set per test would turn a
    50-second gate into a four-minute one for no extra signal.
    """
    with django_db_blocker.unblock():
        return run_eval.run()


def test_labels_and_manifest_agree(results):
    """
    Every manifest entry has labels, and the labels describe the same bytes.

    A label file pointed at a re-issued drawing set produces numbers that look
    like results and are not.
    """
    for entry in run_eval.load_manifest():
        labels = run_eval.load_labels(entry)
        assert labels["bid_set_id"] == entry["id"]
        assert labels["checksum_sha256"] == entry["checksum_sha256"]
        assert len(labels["pages"]) == entry["page_count"]


def test_labels_record_absent_fields_explicitly():
    """
    Absent-accuracy is only measurable if the labels assert absence.

    Omitting a null field from a label file silently deletes the metric, so the
    shape of the labels is itself part of the gate.
    """
    for entry in run_eval.load_manifest():
        labels = run_eval.load_labels(entry)
        for opening in labels["openings"]:
            missing = [f for f in run_eval.EVALUATED_FIELDS if f not in opening]
            assert not missing, (
                f"{entry['id']} door {opening['door_number']}: {missing} not labelled. "
                "Label the value or label it null — never leave it out."
            )


def _baseline() -> dict:
    if not run_eval.BASELINE_PATH.exists():
        pytest.fail(
            "no tests/golden/baseline.json. Run `make eval-baseline`, review the "
            "numbers, and commit it. Without a baseline this gate cannot fail."
        )
    import json

    return json.loads(run_eval.BASELINE_PATH.read_text(encoding="utf-8"))


def test_no_classifier_regression(results):
    """
    The half CI can always measure: triage needs the PDF and nothing else — no
    database rows, no AWS, no spend.
    """
    failures = [
        f
        for f in run_eval.compare_to_baseline(results, _baseline())
        if "classifi" in f or "skipped" in f
    ]
    assert not failures, "classifier regression:\n  " + "\n  ".join(failures)


def test_no_extraction_regression(results):
    """
    The half that needs a processed bid set in the database.

    Under pytest the database is a freshly migrated, empty test database, so this
    normally has nothing to measure and SKIPS — visibly, in the test output,
    rather than passing quietly. The enforcing form is ``make eval-check``, run
    against an environment that has actually processed the golden set.
    """
    if not any(result.get("extraction") for result in results.values()):
        pytest.skip(
            "no extraction run in this database for any golden bid set. "
            "The enforcing gate is `make eval-check` against a real environment."
        )

    failures = [
        f
        for f in run_eval.compare_to_baseline(results, _baseline())
        if "classifi" not in f and "skipped" not in f
    ]
    assert not failures, "extraction regression:\n  " + "\n  ".join(failures)


def test_classifier_finds_every_schedule_page(results):
    """
    Risk R12's asymmetry, asserted directly rather than left to the baseline diff.

    A missed schedule page is a missing opening in a quote. A false positive is
    $0.015. Recall is therefore an absolute floor, not a trend line.
    """
    measured = False
    for bid_set_id, result in results.items():
        classifier = result.get("classifier")
        if classifier is None:
            continue
        measured = True
        assert classifier["schedule_recall"] == 1.0, (
            f"{bid_set_id}: schedule recall {classifier['schedule_recall']:.1%}. "
            f"Pages routed away from OCR entirely: {classifier['schedule_pages_skipped']}"
        )

    if not measured:
        pytest.skip("no golden PDF reachable; classification not measured")


def test_zero_tolerance_flags_are_correct(results):
    """
    §5.8, asserted as an absolute floor rather than a trend line.

    A null fire rating and a *confirmed-unrated* fire rating are different claims,
    and only one of them is safe to act on. The per-field absent-accuracy above
    cannot tell them apart — it only asks whether the value is null — so the flags
    are checked directly here.

    The failure this guards against is not hypothetical. These labels originally
    asserted ``fire_rating_absent: true`` for a schedule that has no rating column
    at all, which would have recorded "confirmed unrated" for four openings nobody
    had confirmed anything about.
    """
    measured = False
    for bid_set_id, result in results.items():
        summary = result.get("extraction_summary")
        if not summary or "zero_tolerance" not in summary:
            continue
        measured = True
        for field_name, zt in summary["zero_tolerance"].items():
            assert zt["absent_flag_mismatched"] == 0, (
                f"{bid_set_id}: {field_name}_absent disagrees with the labels on "
                f"{zt['absent_flag_mismatched']} opening(s). Turning silence into a "
                "positive claim is the §5.8 failure."
            )
            assert zt["review_flag_wrong"] == 0, (
                f"{bid_set_id}: {field_name} was not raised for review on "
                f"{zt['review_flag_wrong']} opening(s). An unconfirmed rating nobody "
                "is asked to look at is the same as no control at all."
            )

    if not measured:
        pytest.skip("no extraction run in this database; see test_no_extraction_regression")


def test_gate_refuses_to_pass_without_a_baseline():
    """
    The meta-test. B12 was a gate that could not fail; this asserts that the
    replacement can.
    """
    fabricated = {
        "fake-set": {
            "classifier": {
                "schedule_recall": 0.5,
                "schedule_precision": 0.5,
                "schedule_pages_skipped": [15],
            },
            "extraction": None,
        }
    }
    baseline = {
        "fake-set": {
            "classifier": {
                "schedule_recall": 1.0,
                "schedule_precision": 0.5,
                "schedule_pages_skipped": [],
            },
            "extraction": {"handing": {"precision": 1.0, "recall": 1.0, "absent_accuracy": 1.0}},
        }
    }
    failures = run_eval.compare_to_baseline(fabricated, baseline)

    assert any("schedule_recall" in f for f in failures)
    assert any("newly skipped" in f for f in failures)
    # And the one that matters most: extraction silently ceasing to be measured
    # must be a failure, not a quiet pass.
    assert any("not\nmeasured" in f or "not measured" in f for f in failures)
