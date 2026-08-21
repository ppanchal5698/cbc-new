"""
Golden-set evaluation (§5.10). The CI quality gate.

    python -m tests.golden.run_eval            # measure and print
    python -m tests.golden.run_eval --check    # ...and fail on regression
    python -m tests.golden.run_eval --write-baseline

This replaces `test_extraction_eval.py`, which computed ``true_positives +=
len(expected)`` and then asserted ``recall > 0.95``. It hardcoded a perfect score
and asserted against it, so it passed unconditionally while standing in for the
quality gate. Everything below is measured; nothing is assumed.

**Per-field, never aggregate.** An aggregate 94% hides that fire rating is at 70%
while door number is at 100%, and those two errors do not cost the same thing.
§5.10's metric list is per field for that reason, and the gate below compares per
field too.

**The metric that matters most is absent-accuracy.** Of the fields genuinely not
stated in the document, how many did the system correctly report as absent rather
than fill in with something plausible? Precision and recall cannot see this: a
system that invents a fire rating for every unrated opening can still post good
recall, because recall only asks about values that exist. NFR-2 is about the
values that do not.

Two halves, measured independently, because they fail independently:

*Classification* runs the real §4 classifier over the real PDF. No AWS, no
database, no spend — so this half is measurable on any developer machine and in
CI, today.

*Extraction* reads a completed run out of the database. When no run exists for a
bid set, that is reported as "not measured" and the extraction metrics are
omitted. It is never reported as a pass. A gate that silently skips is the same
failure as a gate that cannot fail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parent.parent
REPO_ROOT = BACKEND_ROOT.parent
BASELINE_PATH = HERE / "baseline.json"

#: Fields evaluated per opening. Everything §5.7 parses plus the two the
#: zero-tolerance path exists for.
EVALUATED_FIELDS = (
    "size_raw",
    "width_inches",
    "height_inches",
    "handing",
    "fire_rating_raw",
    "fire_rating_minutes",
    "finish_raw",
    "hardware_group",
    "wall_type",
)

#: Opening column -> the ``field_name`` FieldProvenance records it under.
#:
#: They are not the same vocabulary and cannot be. Provenance names the thing the
#: model was asked for (``size``); the Opening names what was stored after parsing
#: (``size_raw``, plus the typed ``width_inches`` and ``height_inches`` derived
#: from it). Several columns therefore share one provenance row.
#:
#: Getting this wrong is not cosmetic. A lookup that misses returns no confidence,
#: which reads as "never flagged", which counts a correctly-flagged wrong value as
#: an **escape** — the one metric that is supposed to mean "reached the customer".
#: The first run of this harness reported 100% escape on size while every size was
#: in fact flagged for review.
PROVENANCE_FIELD = {
    "size_raw": "size",
    "width_inches": "size",
    "height_inches": "size",
    "handing": "handing",
    "fire_rating_raw": "fire_rating",
    "fire_rating_minutes": "fire_rating",
    "finish_raw": "finish",
    "hardware_group": "hardware_group",
    # Never extracted as its own field today; it is read from the wall type
    # legend, which §5.7 has not been asked to parse yet.
    "wall_type": None,
}

#: Regression tolerance. Floating-point re-computation of the same measurement can
#: land a hair off; a real regression is never this small.
EPSILON = 1e-9


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------

@dataclass
class FieldScore:
    """
    Counters for one field across every opening in a bid set.

    The three-way split is deliberate. A field the document states and the system
    got right is not the same event as a field the document omits and the system
    correctly left alone, and collapsing them into one accuracy number is how
    absent-accuracy disappears.
    """

    #: Document states a value, system produced the same value.
    correct: int = 0
    #: Document states a value, system produced a different one.
    wrong: int = 0
    #: Document states a value, system produced nothing.
    missed: int = 0
    #: Document states nothing, system correctly produced nothing.
    absent_correct: int = 0
    #: Document states nothing, system invented a value. The NFR-2 failure.
    hallucinated: int = 0
    #: Wrong or hallucinated AND confidence was above threshold, so nobody looked.
    escaped: int = 0
    #: Above-threshold count, the denominator for escape rate.
    unflagged: int = 0

    @property
    def present_total(self) -> int:
        return self.correct + self.wrong + self.missed

    @property
    def absent_total(self) -> int:
        return self.absent_correct + self.hallucinated

    @property
    def produced(self) -> int:
        """Values the system emitted for this field, right or wrong."""
        return self.correct + self.wrong + self.hallucinated

    @property
    def precision(self) -> float | None:
        return self.correct / self.produced if self.produced else None

    @property
    def recall(self) -> float | None:
        return self.correct / self.present_total if self.present_total else None

    @property
    def absent_accuracy(self) -> float | None:
        """★ Of the fields genuinely absent, how many were reported absent."""
        return self.absent_correct / self.absent_total if self.absent_total else None

    @property
    def escape_rate(self) -> float | None:
        """Wrong values that were NOT flagged, over everything left unflagged."""
        return self.escaped / self.unflagged if self.unflagged else None

    def as_dict(self) -> dict:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "absent_accuracy": self.absent_accuracy,
            "escape_rate": self.escape_rate,
            "counts": {
                "correct": self.correct,
                "wrong": self.wrong,
                "missed": self.missed,
                "absent_correct": self.absent_correct,
                "hallucinated": self.hallucinated,
                "escaped": self.escaped,
            },
        }


@dataclass
class ClassifierScore:
    """
    §4 triage, scored the way Risk R12 weights it.

    Recall on the schedule classes is the number that gates. A false positive
    costs $0.015 of Textract; a false negative costs an opening that never
    reaches the quote. Those are not symmetric and the report should not pretend
    they are.
    """

    schedule_expected: int = 0
    schedule_found: int = 0
    schedule_false_positives: int = 0
    #: Expected schedule pages the classifier routed away from OCR entirely. The
    #: expensive failure: not a misread page, an unread one.
    schedule_pages_skipped: list[int] = field(default_factory=list)
    confusion: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: str = "0"
    naive_cost_usd: str = "0"

    @property
    def recall(self) -> float | None:
        return self.schedule_found / self.schedule_expected if self.schedule_expected else None

    @property
    def precision(self) -> float | None:
        produced = self.schedule_found + self.schedule_false_positives
        return self.schedule_found / produced if produced else None

    def as_dict(self) -> dict:
        return {
            "schedule_recall": self.recall,
            "schedule_precision": self.precision,
            "schedule_pages_skipped": self.schedule_pages_skipped,
            "confusion": self.confusion,
            "estimated_cost_usd": self.estimated_cost_usd,
            "naive_cost_usd": self.naive_cost_usd,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_manifest() -> list[dict]:
    return yaml.safe_load((HERE / "manifest.yaml").read_text(encoding="utf-8"))["bid_sets"]


def load_labels(entry: dict) -> dict:
    return yaml.safe_load((HERE / entry["labels"]).read_text(encoding="utf-8"))


def resolve_pdf(entry: dict) -> Path | None:
    """
    Locate the PDF. Local path first, then S3.

    Returns None rather than raising: a machine without the confidential bid set
    should still be able to run the extraction half of the eval and read the
    report, and the classifier section says plainly that it was not measured.
    """
    if entry.get("local_path"):
        candidate = REPO_ROOT / entry["local_path"]
        if candidate.exists():
            return candidate
        # Inside the container the repo root is not mounted; /app is.
        candidate = BACKEND_ROOT / entry["local_path"]
        if candidate.exists():
            return candidate

    key = entry.get("s3_key")
    if not key:
        return None

    import tempfile

    import boto3

    from shared.config import get_settings

    settings = get_settings()
    target = Path(tempfile.gettempdir()) / f"golden-{entry['id']}.pdf"
    if target.exists():
        return target
    try:
        boto3.client("s3", **settings.boto_kwargs_for("s3")).download_file(
            settings.s3_derived_bucket, key, str(target)
        )
    except Exception as exc:  # noqa: BLE001 - absence is a reportable state, not a crash
        print(f"  ! could not fetch {key} from S3: {exc}", file=sys.stderr)
        return None
    return target


def verify_checksum(path: Path, expected: str) -> bool:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


# ---------------------------------------------------------------------------
# Classification half — real classifier, real PDF, no spend
# ---------------------------------------------------------------------------

def evaluate_classification(pdf: Path, labels: dict) -> ClassifierScore:
    from pipeline.routing import load_routing_table
    from pipeline.stages.preprocess import analyze_document
    from shared.enums import OCRRoute, PageClass

    table = load_routing_table()
    # No cost guard here: the eval is measuring what triage decides, and a budget
    # abort would hide the very number being measured.
    probes = analyze_document(pdf.read_bytes(), table=table, max_cost_usd=None)

    schedule_classes = {c.value for c in PageClass.schedules()}
    expected = {int(p): v["class"] for p, v in labels["pages"].items()}
    actual = {p.page_number: p.page_class for p in probes}
    routes = {p.page_number: p.ocr_route for p in probes}

    score = ClassifierScore()
    for page, expected_class in sorted(expected.items()):
        got = actual.get(page, "MISSING")
        key = f"{expected_class} -> {got}"
        score.confusion[key] = score.confusion.get(key, 0) + 1

        if expected_class in schedule_classes:
            score.schedule_expected += 1
            if got in schedule_classes:
                score.schedule_found += 1
            elif routes.get(page) == OCRRoute.SKIP.value:
                score.schedule_pages_skipped.append(page)
        elif got in schedule_classes:
            score.schedule_false_positives += 1

    total = sum((p.ocr_cost_estimate for p in probes), Decimal("0"))
    score.estimated_cost_usd = f"{total:.4f}"
    # What the naive "AnalyzeDocument the whole set" design would have cost. The
    # difference is the entire justification for §4, so the report states it
    # rather than leaving it as an assertion in a design document.
    score.naive_cost_usd = f"{Decimal(len(probes)) * Decimal('0.015'):.4f}"
    return score


# ---------------------------------------------------------------------------
# Extraction half — reads a completed run out of the database
# ---------------------------------------------------------------------------

def _normalise(value) -> str | None:
    """
    Compare on meaning, not on formatting.

    Case and surrounding whitespace are presentation. ``"GROUP 1"`` and
    ``"group 1"`` are the same hardware group and scoring them as a miss would
    make the gate noisy without making it stricter.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return " ".join(text.upper().split())


def evaluate_extraction(entry: dict, labels: dict) -> tuple[dict, dict] | None:
    """
    Score the most recent extraction run for this bid set.

    Returns ``None`` when no run exists — reported as "not measured", never as a
    pass.
    """
    from django.db.models import Q
    from openings.models import ExtractionRun, FieldProvenance, Opening
    from projects.models import Document

    from shared.config import get_settings
    from shared.enums import ReviewState

    document = (
        Document.objects.filter(
            Q(filename=entry["filename"]) | Q(checksum_sha256=entry["checksum_sha256"])
        )
        .order_by("-created_at")
        .first()
    )
    if document is None:
        return None

    run = (
        ExtractionRun.objects.filter(document=document).order_by("-started_at").first()
    )
    if run is None:
        return None

    settings = get_settings()
    thresholds = {
        "fire_rating": settings.confidence_threshold_fire_rating,
        "handing": settings.confidence_threshold_handing,
    }

    actual_openings = {
        _normalise(o.door_number): o
        for o in Opening.objects.filter(extraction_run=run)
    }
    expected_openings = {_normalise(o["door_number"]): o for o in labels["openings"]}

    provenance = {}
    for prov in FieldProvenance.objects.filter(extraction_run=run).select_related("opening"):
        key = (_normalise(prov.opening.door_number) if prov.opening else None, prov.field_name)
        provenance[key] = prov

    scores = {name: FieldScore() for name in EVALUATED_FIELDS}

    for door, expected in expected_openings.items():
        opening = actual_openings.get(door)
        for name in EVALUATED_FIELDS:
            score = scores[name]
            want = _normalise(expected.get(name))
            got = _normalise(getattr(opening, name, None)) if opening else None

            if want is not None:
                if got == want:
                    score.correct += 1
                elif got is None:
                    score.missed += 1
                else:
                    score.wrong += 1
            elif got is None:
                score.absent_correct += 1
            else:
                score.hallucinated += 1

            # Escape rate: a wrong or invented value that nobody was asked to look
            # at. A wrong value the system flagged is the system working.
            is_error = (want is not None and got != want) or (want is None and got is not None)
            prov_field = PROVENANCE_FIELD.get(name, name)
            prov = provenance.get((door, prov_field)) if prov_field else None
            confidence = prov.final_confidence if prov else None
            threshold = thresholds.get(prov_field, settings.confidence_threshold_default)
            flagged = confidence is not None and float(confidence) < threshold
            if not flagged:
                score.unflagged += 1
                if is_error:
                    score.escaped += 1

    # Openings invented wholesale. Counted separately from field errors: a
    # fabricated door 04 is not a bad size value, it is a line item that will be
    # quoted, ordered, and delivered to a wall that has no opening in it.
    spurious = sorted(set(actual_openings) - set(expected_openings))
    absent_openings = sorted(set(expected_openings) - set(actual_openings))

    # Citation validity = fields the §5.6 gate did NOT refuse. REJECTED means a
    # fabricated element id, or a value not present in the text it cited.
    citations_total = FieldProvenance.objects.filter(extraction_run=run).count()
    citations_rejected = FieldProvenance.objects.filter(
        extraction_run=run, review_state=ReviewState.REJECTED.value
    ).count()

    metrics = getattr(run, "metrics", None)
    summary = {
        "extraction_run_id": str(run.id),
        "prompt_version": run.prompt_version,
        "model_id": run.model_id,
        "openings_expected": len(expected_openings),
        "openings_found": len(actual_openings),
        "openings_spurious": spurious,
        "openings_absent": absent_openings,
        "citation_validity": (
            (citations_total - citations_rejected) / citations_total if citations_total else None
        ),
        "citation_rejection_rate": (
            metrics.citation_rejection_rate if metrics is not None else None
        ),
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cached_input_tokens": run.cached_input_tokens,
        "cost_usd": str(run.cost_usd) if run.cost_usd is not None else None,
        "latency_seconds": (
            (run.completed_at - run.started_at).total_seconds() if run.completed_at else None
        ),
    }
    return {name: score.as_dict() for name, score in scores.items()}, summary


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pct(value: float | None) -> str:
    return "     —" if value is None else f"{value:6.1%}"


def print_report(results: dict) -> None:
    for bid_set_id, result in results.items():
        print()
        print("=" * 78)
        print(f"  {bid_set_id}")
        print("=" * 78)

        classifier = result.get("classifier")
        if classifier is None:
            print("\n  CLASSIFICATION — not measured (PDF unavailable)")
        else:
            print("\n  CLASSIFICATION (§4 triage)")
            print(f"    schedule recall     {_pct(classifier['schedule_recall'])}   ← the gate (R12)")
            print(f"    schedule precision  {_pct(classifier['schedule_precision'])}   "
                  "(a miss here costs $0.015)")
            skipped = classifier["schedule_pages_skipped"]
            print(f"    schedule pages skipped entirely: {skipped or 'none'}")
            print(f"    triaged cost ${classifier['estimated_cost_usd']} vs "
                  f"${classifier['naive_cost_usd']} reading every page")
            print("\n    confusion (expected -> actual):")
            for key, count in sorted(classifier["confusion"].items(), key=lambda kv: -kv[1]):
                print(f"      {count:3d}  {key}")

        extraction = result.get("extraction")
        if extraction is None:
            print("\n  EXTRACTION — NOT MEASURED: no extraction run in the database for this")
            print("               bid set. Process it, then re-run. This is not a pass.")
            continue

        summary = result["extraction_summary"]
        print(f"\n  EXTRACTION  (run {summary['extraction_run_id'][:8]}, "
              f"prompt {summary['prompt_version']})")
        print(f"    openings expected {summary['openings_expected']}, "
              f"found {summary['openings_found']}")
        if summary["openings_spurious"]:
            print(f"    ✗ FABRICATED openings: {summary['openings_spurious']}")
        if summary["openings_absent"]:
            print(f"    ✗ MISSED openings:     {summary['openings_absent']}")

        print()
        print(f"    {'field':<22} {'prec':>7} {'recall':>7} {'absent':>7} {'escape':>7}   counts")
        print(f"    {'-' * 22} {'-' * 7} {'-' * 7} {'-' * 7} {'-' * 7}   {'-' * 30}")
        for name in EVALUATED_FIELDS:
            score = extraction[name]
            counts = score["counts"]
            detail = (
                f"ok={counts['correct']} wrong={counts['wrong']} missed={counts['missed']} "
                f"absent_ok={counts['absent_correct']} halluc={counts['hallucinated']}"
            )
            print(
                f"    {name:<22} {_pct(score['precision'])} {_pct(score['recall'])} "
                f"{_pct(score['absent_accuracy'])} {_pct(score['escape_rate'])}   {detail}"
            )

        print(f"\n    citation validity  {_pct(summary['citation_validity'])}")
        print(f"    citation rejection {_pct(summary['citation_rejection_rate'])}   "
              "← rises when a prompt or model drifts")
        print(f"    cost ${summary['cost_usd']}  "
              f"tokens in={summary['input_tokens']} (cached {summary['cached_input_tokens']}) "
              f"out={summary['output_tokens']}  "
              f"latency {summary['latency_seconds']}s")

    print()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

#: Metrics where a drop is a regression. Escape rate is inverted — it is the one
#: number where lower is better.
HIGHER_IS_BETTER = ("precision", "recall", "absent_accuracy")


def compare_to_baseline(results: dict, baseline: dict) -> list[str]:
    """Every way the current run is worse than the recorded baseline."""
    failures: list[str] = []

    for bid_set_id, result in results.items():
        before = baseline.get(bid_set_id)
        if before is None:
            continue

        old_class, new_class = before.get("classifier"), result.get("classifier")
        if old_class and not new_class:
            # Same rule as extraction below: a measurement that was in the
            # baseline and is missing now is a regression in the gate itself. The
            # usual cause is the golden PDF not being reachable, which makes the
            # build green for the wrong reason.
            failures.append(
                f"{bid_set_id}: classification was measured in the baseline and is not "
                "measured now — the golden PDF was not reachable."
            )
        if old_class and new_class:
            for metric in ("schedule_recall", "schedule_precision"):
                was, now = old_class.get(metric), new_class.get(metric)
                if was is not None and now is not None and now < was - EPSILON:
                    failures.append(
                        f"{bid_set_id}: classifier {metric} {was:.1%} -> {now:.1%}"
                    )
            newly_skipped = set(new_class["schedule_pages_skipped"]) - set(
                old_class.get("schedule_pages_skipped", [])
            )
            if newly_skipped:
                failures.append(
                    f"{bid_set_id}: schedule pages newly skipped entirely: "
                    f"{sorted(newly_skipped)}"
                )

        old_ext, new_ext = before.get("extraction"), result.get("extraction")
        if old_ext and not new_ext:
            failures.append(
                f"{bid_set_id}: extraction was measured in the baseline and is not "
                "measured now. A gate that stops running is a gate that stops working."
            )
        if not (old_ext and new_ext):
            continue

        for name in EVALUATED_FIELDS:
            was_field, now_field = old_ext.get(name), new_ext.get(name)
            if not (was_field and now_field):
                continue
            for metric in HIGHER_IS_BETTER:
                was, now = was_field.get(metric), now_field.get(metric)
                if was is not None and now is not None and now < was - EPSILON:
                    failures.append(f"{bid_set_id}: {name}.{metric} {was:.1%} -> {now:.1%}")
            was, now = was_field.get("escape_rate"), now_field.get("escape_rate")
            if was is not None and now is not None and now > was + EPSILON:
                failures.append(f"{bid_set_id}: {name}.escape_rate {was:.1%} -> {now:.1%}")

    return failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _setup_django() -> bool:
    """Bring Django up so the extraction half can read the database."""
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    api_path = str(BACKEND_ROOT / "api")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    try:
        django.setup()
        return True
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        print(f"  ! Django unavailable, extraction half skipped: {exc}", file=sys.stderr)
        return False


def run(bid_set_filter: str | None = None) -> dict:
    results: dict = {}
    django_ready = _setup_django()

    for entry in load_manifest():
        if bid_set_filter and entry["id"] != bid_set_filter:
            continue

        labels = load_labels(entry)
        result: dict = {"classifier": None, "extraction": None}

        pdf = resolve_pdf(entry)
        if pdf is None:
            print(f"  ! {entry['id']}: PDF not found locally or in S3", file=sys.stderr)
        elif not verify_checksum(pdf, entry["checksum_sha256"]):
            # Refuse rather than measure. Labels written against different bytes
            # produce a number that looks like a result and is not one.
            print(
                f"  ! {entry['id']}: checksum mismatch — {pdf} is not the document these "
                "labels describe. Skipping.",
                file=sys.stderr,
            )
        else:
            result["classifier"] = evaluate_classification(pdf, labels).as_dict()

        if django_ready:
            extraction = evaluate_extraction(entry, labels)
            if extraction is not None:
                result["extraction"], result["extraction_summary"] = extraction

        results[entry["id"]] = result

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden-set evaluation (§5.10)")
    parser.add_argument("--check", action="store_true", help="fail on regression vs the baseline")
    parser.add_argument("--write-baseline", action="store_true", help="record this run as the baseline")
    parser.add_argument("--bid-set", help="evaluate one bid set by id")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of the report")
    args = parser.parse_args(argv)

    results = run(args.bid_set)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print_report(results)

    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"baseline written to {BASELINE_PATH}")
        return 0

    if not args.check:
        return 0

    if not BASELINE_PATH.exists():
        print(
            "no baseline recorded. Run --write-baseline once, review the numbers, and "
            "commit tests/golden/baseline.json.",
            file=sys.stderr,
        )
        return 1

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    failures = compare_to_baseline(results, baseline)
    if failures:
        print("REGRESSION:", file=sys.stderr)
        for failure in failures:
            print(f"  ✗ {failure}", file=sys.stderr)
        return 1

    print("no regression against the recorded baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
