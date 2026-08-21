"""
Confidence threshold calibration (§5.9, Risk R7, open item C14).

    python ops/scripts/calibrate_threshold.py [--field handing] [--min-samples 20]

**This script does not choose a threshold. CBC chooses the threshold.** What it
produces is the curve they need to choose from, phrased the way the decision is
actually made: *"flag X% of fields, and roughly one wrong value in Y gets
through."*

0.80 is a placeholder. It was picked because it looks like a confidence number,
not because anyone measured what it costs. The version of this script it replaces
made that worse by sweeping six thresholds over eight hardcoded tuples and
printing the result as though it meant something — and its docstring sourced
those from ``CorrectionEvent``, a table that no longer exists.

Per field, never global. Getting a finish code wrong means a re-order; getting a
fire rating wrong means an opening that fails inspection, or does not hold a fire
back. §5.8 already holds rating and handing to stricter thresholds; this is how
the numbers behind that stop being guesses.

Ground truth is estimator behaviour: a ``feedback`` row against a field means a
human looked at the value and changed it, which is the only definition of "wrong"
the system actually possesses (FR-13).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _django_setup import setup  # noqa: E402

setup()

from feedback.models import Feedback  # noqa: E402
from openings.models import FieldProvenance  # noqa: E402

from shared.config import get_settings  # noqa: E402
from shared.enums import FeedbackEntity, ReviewState  # noqa: E402

#: 0.50 to 0.99 in 0.01 steps (§5.9). Finer than anyone will operate at, because
#: the interesting part of the curve is usually a narrow band and a coarse sweep
#: walks straight past it.
THRESHOLDS = [round(0.50 + 0.01 * i, 2) for i in range(50)]

#: Below this many labelled samples a field's curve is noise wearing a percent
#: sign. Reported as insufficient rather than plotted.
DEFAULT_MIN_SAMPLES = 20


def current_thresholds(settings) -> dict[str, float]:
    """
    The operating point in force today, keyed by **provenance** field name.

    Provenance records ``fire_rating``; the Opening column is ``fire_rating_raw``.
    They are different vocabularies, and keying this table by the Opening name
    silently prints the 0.80 default beside the two fields §5.8 holds to 0.95 —
    which is the one place an operator would notice the stricter floor exists.
    """
    return {
        "fire_rating": settings.confidence_threshold_fire_rating,
        "handing": settings.confidence_threshold_handing,
    }


def pool_composition() -> dict[str, int]:
    """``{prompt_version: usable sample count}`` across every run in the database."""
    from django.db.models import Count

    rows = (
        FieldProvenance.objects.exclude(final_confidence__isnull=True)
        .values("extraction_run__prompt_version")
        .annotate(n=Count("id"))
        .order_by("extraction_run__prompt_version")
    )
    return {r["extraction_run__prompt_version"]: r["n"] for r in rows}


def gather_samples(prompt_version: str) -> dict[str, list[tuple[float, bool]]]:
    """
    ``{field_name: [(confidence, was_wrong), ...]}`` for ONE prompt version.

    "Wrong" means an estimator changed the value, or the §5.6 validation gate
    rejected it. Both are cases where the system produced something it should not
    have, which is exactly what a threshold is meant to catch.

    **Scoped to a single prompt version, and that is not fussiness.** A threshold
    is a property of the prompt and model that produced the confidences, not of
    the field name. Pooling versions lets a superseded prompt set the operating
    point for the one actually running: extraction v1 returned door sizes as the
    width alone and scored them at 0.39, so mixing its rows into a v2 calculation
    would drag the size threshold down to accommodate a bug that no longer exists,
    and the resulting number would look measured.
    """
    corrected = set(
        Feedback.objects.filter(
            entity_type=FeedbackEntity.OPENING.value, field_provenance__isnull=False
        ).values_list("field_provenance_id", flat=True)
    )

    samples: dict[str, list[tuple[float, bool]]] = {}
    provenances = (
        FieldProvenance.objects.exclude(final_confidence__isnull=True)
        .filter(extraction_run__prompt_version=prompt_version)
        .only("id", "field_name", "final_confidence", "review_state")
    )

    for prov in provenances.iterator():
        was_wrong = prov.id in corrected or prov.review_state in (
            ReviewState.CORRECTED.value,
            ReviewState.REJECTED.value,
        )
        samples.setdefault(prov.field_name, []).append((float(prov.final_confidence), was_wrong))

    return samples


def curve(samples: list[tuple[float, bool]]) -> list[dict]:
    """Flag rate and escape rate at every threshold."""
    total = len(samples)
    errors = sum(1 for _, wrong in samples if wrong)

    rows = []
    for threshold in THRESHOLDS:
        # A field is flagged for review when its confidence is below the threshold.
        flagged = sum(1 for confidence, _ in samples if confidence < threshold)
        # An error escapes when it was wrong AND was not flagged, so no human ever
        # looked at it. This is the number that reaches a customer.
        escaped = sum(1 for confidence, wrong in samples if wrong and confidence >= threshold)
        rows.append(
            {
                "threshold": threshold,
                "flag_rate": flagged / total if total else 0.0,
                "flagged": flagged,
                "escape_rate": escaped / errors if errors else 0.0,
                "escaped": escaped,
                # What CBC actually asks: "one bad value in how many?"
                "one_in": (total - flagged) / escaped if escaped else None,
            }
        )
    return rows


def recommend(rows: list[dict], max_escape_rate: float) -> dict | None:
    """
    Cheapest threshold meeting an escape-rate target.

    Cheapest means fewest fields flagged: review time is the cost side of this
    trade, and a threshold that catches everything by flagging everything has
    simply moved the work back to the estimator.
    """
    viable = [row for row in rows if row["escape_rate"] <= max_escape_rate]
    return min(viable, key=lambda row: row["flag_rate"]) if viable else None


def print_curve(field: str, samples: list[tuple[float, bool]], current: float) -> None:
    rows = curve(samples)
    errors = sum(1 for _, wrong in samples if wrong)

    print()
    print("=" * 78)
    print(f"  {field}")
    print(f"  {len(samples)} reviewed values, {errors} corrected by an estimator "
          f"({errors / len(samples):.1%})")
    print(f"  current threshold {current:.2f}")
    print("=" * 78)
    print(f"  {'thresh':>7} {'flagged':>9} {'escapes':>9} {'escape rate':>12}   "
          f"{'~1 bad value in':>16}")
    print(f"  {'-' * 7} {'-' * 9} {'-' * 9} {'-' * 12}   {'-' * 16}")

    for row in rows:
        # Print every 5th step plus the current operating point, so the table is
        # readable without hiding where the system is running today.
        if round(row["threshold"] * 100) % 5 and abs(row["threshold"] - current) > 1e-9:
            continue
        marker = "  <- current" if abs(row["threshold"] - current) < 1e-9 else ""
        one_in = f"{row['one_in']:.0f}" if row["one_in"] else "none escaped"
        print(f"  {row['threshold']:7.2f} {row['flag_rate']:8.1%} {row['escaped']:9d} "
              f"{row['escape_rate']:11.1%}   {one_in:>16}{marker}")

    print()
    for target in (0.10, 0.05, 0.01):
        pick = recommend(rows, target)
        if pick is None:
            print(f"    no threshold in 0.50-0.99 holds escapes under {target:.0%} "
                  "— the model, not the threshold, is the problem here")
        else:
            print(f"    to let through <{target:>4.0%} of errors: threshold "
                  f"{pick['threshold']:.2f}, flagging {pick['flag_rate']:.1%} of fields "
                  f"({pick['flagged']} of {len(samples)})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confidence calibration (§5.9)")
    parser.add_argument("--field", help="one field only")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument(
        "--prompt-version",
        help="calibrate this prompt version instead of the one currently configured",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    current = current_thresholds(settings)

    version = args.prompt_version or settings.extraction_prompt_version
    pool = pool_composition()

    print()
    print(f"Calibrating prompt version {version!r}.")
    if pool:
        print("Sample pool in this database, by prompt version:")
        for found, count in sorted(pool.items()):
            mark = "  <- calibrating" if found == version else "  (excluded)"
            print(f"    {found or '(none)':<10} {count:5d} scored fields{mark}")
        if len(pool) > 1:
            print()
            print("  Only the selected version is used. A threshold belongs to the prompt")
            print("  that produced the confidences, so pooling versions would let a")
            print("  superseded prompt set the operating point for the one in force.")

    samples = gather_samples(version)
    if args.field:
        samples = {k: v for k, v in samples.items() if k == args.field}

    if not samples:
        print()
        print("No reviewed extractions in this database, so there is nothing to calibrate.")
        print()
        print("This is the expected state before go-live. The curve needs estimators to have")
        print("corrected real values: every review-UI edit writes a feedback row (FR-13), and")
        print("those rows are the ground truth. Until then CONFIDENCE_THRESHOLD_DEFAULT stays")
        print(f"at its placeholder {settings.confidence_threshold_default:.2f} — which is a")
        print("placeholder, not a measurement, and should be described that way to CBC.")
        print()
        return 0

    print()
    print("Confidence calibration — flag rate vs escape rate, per field (§5.9)")
    print()
    print("  Read this with CBC and pick an operating point per field. The question is")
    print('  "how many fields are we willing to review to catch how many errors", and it is')
    print("  a business decision about estimator time, not an engineering one.")

    insufficient = []
    for field, field_samples in sorted(samples.items()):
        if len(field_samples) < args.min_samples:
            insufficient.append((field, len(field_samples)))
            continue
        print_curve(field, field_samples, current.get(field, settings.confidence_threshold_default))

    if insufficient:
        print()
        print("-" * 78)
        print(f"  Not enough reviewed samples to calibrate (need {args.min_samples}):")
        for field, count in insufficient:
            print(f"    {field:<28} {count} sample(s)")
        print("  Reported rather than plotted: a curve over a handful of points would look")
        print("  like an answer and would not be one.")

    print()
    print("Nothing here changes a threshold. Set the chosen values in SSM as")
    print("CONFIDENCE_THRESHOLD_DEFAULT / _FIRE_RATING / _HANDING once CBC has signed off.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
