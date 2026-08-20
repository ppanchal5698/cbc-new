"""
Per-bid-set AWS cost attribution (§10.3, NFR-6).

    python ops/scripts/cost_report.py [--project-id UUID] [--since 2026-01-01]

Reads what actually happened. The version this replaces printed a hardcoded
`total_documents = 50` and a made-up token average, which is worse than no
report: it produced a number with a dollar sign in front of it that nobody had
measured, and "within cost guardrails" is a dangerous thing to print from mock
data.

Three sources, because the money is spent in three places:

* ``document_manifest`` — one row per page, carrying the route triage chose and
  the cost that route implies. This is where the §4 saving is visible, and it is
  the only place it *is* visible: a document that cost $0.12 instead of $0.98
  looks identical from the outside.
* ``pipeline_jobs`` — per-stage estimate and actual, including retries. Attempts
  matter: a job redelivered three times is billed three times unless the
  idempotency guard held, and this report is how you find out that it did not.
* ``extraction_runs`` — Bedrock tokens and cost, with cached input broken out
  separately since prompt caching is the difference between a $0.03 document and
  a $0.30 one.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _django_setup import setup  # noqa: E402

setup()

from django.db.models import Count, Sum  # noqa: E402
from openings.models import ExtractionRun  # noqa: E402
from projects.models import Document, DocumentManifest, PipelineJob  # noqa: E402

from shared.config import get_settings  # noqa: E402
from shared.enums import OCRRoute, PipelineJobStatus  # noqa: E402

ZERO = Decimal("0")


def money(value) -> str:
    return f"${Decimal(value or 0):,.4f}"


def document_costs(document: Document) -> dict:
    """Everything spent on one document, by source."""
    pages = DocumentManifest.objects.filter(document=document)
    by_route = {
        row["ocr_route"]: row
        for row in pages.values("ocr_route").annotate(
            pages=Count("id"), cost=Sum("ocr_cost_estimate")
        )
    }

    jobs = PipelineJob.objects.filter(document=document)
    runs = ExtractionRun.objects.filter(document=document)

    ocr_cost = sum((Decimal(r["cost"] or 0) for r in by_route.values()), ZERO)
    # Prefer the actual where a stage recorded one; fall back to the estimate so a
    # stage that has not reported yet is visible rather than counted as free.
    job_cost = sum(
        (Decimal(j.cost_actual if j.cost_actual is not None else (j.cost_estimate or 0)) for j in jobs),
        ZERO,
    )
    llm_cost = sum((Decimal(r.cost_usd or 0) for r in runs), ZERO)

    return {
        "document": document,
        "page_count": pages.count(),
        "by_route": by_route,
        "ocr_cost": ocr_cost,
        "job_cost": job_cost,
        "llm_cost": llm_cost,
        "total": ocr_cost + llm_cost,
        "retried_jobs": [j for j in jobs if j.attempt > 1],
        "failed_jobs": [
            j
            for j in jobs
            if j.status in (PipelineJobStatus.FAILED.value, PipelineJobStatus.QUARANTINED.value)
        ],
        "input_tokens": sum(r.input_tokens for r in runs),
        "cached_input_tokens": sum(r.cached_input_tokens for r in runs),
        "output_tokens": sum(r.output_tokens for r in runs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-bid-set cost attribution (§10.3)")
    parser.add_argument("--project-id", help="restrict to one project")
    parser.add_argument("--since", help="ISO date; only documents created on or after")
    args = parser.parse_args(argv)

    settings = get_settings()
    guard = settings.max_ocr_cost_per_document_usd

    documents = Document.objects.select_related("project").order_by("created_at")
    if args.project_id:
        documents = documents.filter(project_id=args.project_id)
    if args.since:
        documents = documents.filter(created_at__gte=datetime.fromisoformat(args.since))

    if not documents.exists():
        print("No documents match. Nothing has been processed, or the filters exclude everything.")
        print("This is an empty result, not a zero-cost result.")
        return 0

    print()
    print("=" * 92)
    print("  CBC Copilot — cost report")
    print(f"  per-document OCR guard: {money(guard)}   (MAX_OCR_COST_PER_DOCUMENT_USD)")
    print("=" * 92)

    totals = {"ocr": ZERO, "llm": ZERO, "pages": 0, "skipped": 0, "docs": 0}
    over_guard = []

    for document in documents:
        report = document_costs(document)
        if report["page_count"] == 0 and report["total"] == ZERO:
            continue

        totals["docs"] += 1
        totals["ocr"] += report["ocr_cost"]
        totals["llm"] += report["llm_cost"]
        totals["pages"] += report["page_count"]
        skipped = report["by_route"].get(OCRRoute.SKIP.value, {}).get("pages", 0)
        totals["skipped"] += skipped

        print()
        print(f"  {document.project.name} — {document.filename}")
        print(f"    {report['page_count']} pages")
        for route, row in sorted(report["by_route"].items()):
            share = row["pages"] / report["page_count"] if report["page_count"] else 0
            print(f"      {route:<16} {row['pages']:4d} pages ({share:5.1%})  {money(row['cost'])}")

        print(f"    OCR {money(report['ocr_cost'])}   "
              f"Bedrock {money(report['llm_cost'])}   "
              f"total {money(report['total'])}")

        if report["input_tokens"]:
            cached_share = report["cached_input_tokens"] / report["input_tokens"]
            print(f"    tokens in={report['input_tokens']:,} "
                  f"(cached {report['cached_input_tokens']:,}, {cached_share:.0%}) "
                  f"out={report['output_tokens']:,}")

        if report["retried_jobs"]:
            print(f"    ⚠ {len(report['retried_jobs'])} job(s) retried: "
                  + ", ".join(f"{j.stage} x{j.attempt}" for j in report["retried_jobs"])
                  + "  — confirm the idempotency guard held, or this is double spend")
        if report["failed_jobs"]:
            print(f"    ⚠ {len(report['failed_jobs'])} job(s) failed or quarantined: "
                  + ", ".join(f"{j.stage}/{j.status}" for j in report["failed_jobs"]))

        if report["ocr_cost"] > guard:
            over_guard.append((document, report["ocr_cost"]))
            print(f"    ✗ OCR cost {money(report['ocr_cost'])} EXCEEDS the "
                  f"{money(guard)} per-document guard")

    grand = totals["ocr"] + totals["llm"]
    print()
    print("-" * 92)
    print(f"  {totals['docs']} documents, {totals['pages']:,} pages")
    if totals["pages"]:
        # The §4 saving, measured rather than claimed. The counterfactual is the
        # naive design: AnalyzeDocument on every page.
        naive = Decimal(totals["pages"]) * Decimal("0.015")
        print(f"  pages skipped by triage: {totals['skipped']:,} "
              f"({totals['skipped'] / totals['pages']:.1%})")
        print(f"  OCR {money(totals['ocr'])} vs {money(naive)} reading every page "
              f"— saved {money(naive - totals['ocr'])}")
    print(f"  Bedrock {money(totals['llm'])}")
    print(f"  TOTAL {money(grand)}")
    if totals["docs"]:
        print(f"  average per document {money(grand / totals['docs'])}")

    if over_guard:
        print()
        print(f"  ✗ {len(over_guard)} document(s) over the per-document OCR guard:")
        for document, cost in over_guard:
            print(f"      {document.filename}  {money(cost)}")
        return 1

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
