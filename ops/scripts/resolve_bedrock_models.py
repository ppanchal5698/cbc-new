"""
Resolve Bedrock model IDs at deploy time and pin them in SSM (C5, D12).

    python ops/scripts/resolve_bedrock_models.py --environment dev
    python ops/scripts/resolve_bedrock_models.py --environment dev --dry-run

**Never hardcode a model ID.** Not in `.env`, not in `settings.py`, not in a
Terraform variable. Three reasons, in order of how much they hurt:

1. A run that cannot name the exact model version that produced a value cannot be
   audited (NFR-3). A quote sent six months ago has to remain explainable, and
   "Claude" is not an explanation.
2. Model IDs are not stable identifiers. The current Claude models are reachable
   only through **inference profiles** (`us.anthropic.…`), not bare
   foundation-model IDs, and which profiles exist depends on the account and the
   region.
3. A pinned ID that silently stops resolving fails at the first extraction call,
   in production, on a real bid set.

So: discover what this account can actually invoke, choose deliberately, write the
choice to SSM as the pinned value, and record it on every `extraction_runs` row.

Two IDs are pinned, matching the two-pass design (§5.3):

* `BEDROCK_MODEL_ID_CHEAP` — Pass A, locating schedule tables from a header
  inventory. Haiku is sufficient and is called once per document.
* `BEDROCK_MODEL_ID` — Pass B, extracting one table at a time with mandatory
  citations. This is the call whose accuracy the whole traceability contract rests
  on, so it gets the strongest available model.
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError

#: Preference order for the extraction model, strongest first. Matched as a
#: substring against inference-profile ids, so a version bump is picked up without
#: editing this list.
PREMIUM_PREFERENCE = (
    "anthropic.claude-opus-4",
    "anthropic.claude-sonnet-4",
    "anthropic.claude-3-7-sonnet",
    "anthropic.claude-3-5-sonnet",
)

#: Preference order for the cheap locate pass.
CHEAP_PREFERENCE = (
    "anthropic.claude-haiku-4",
    "anthropic.claude-3-5-haiku",
    "anthropic.claude-3-haiku",
)


def list_invocable(region: str) -> list[dict]:
    """
    Everything this account can actually invoke, profiles first.

    Inference profiles are listed ahead of bare foundation models because for the
    current generation they are the only thing that works — a bare
    foundation-model id returns a validation error telling you to use a profile.
    """
    bedrock = boto3.client("bedrock", region_name=region)
    candidates: list[dict] = []

    try:
        paginator = bedrock.get_paginator("list_inference_profiles")
        pages = paginator.paginate()
    except Exception:  # noqa: BLE001 - older botocore has no paginator for this
        pages = [bedrock.list_inference_profiles()]

    for page in pages:
        for profile in page.get("inferenceProfileSummaries", []):
            if profile.get("status") not in (None, "ACTIVE"):
                continue
            candidates.append(
                {
                    "id": profile["inferenceProfileId"],
                    "name": profile.get("inferenceProfileName", ""),
                    "kind": "inference-profile",
                }
            )

    for model in bedrock.list_foundation_models(byProvider="anthropic").get(
        "modelSummaries", []
    ):
        # TOOL is not optional: extraction is a tool call with a mandatory
        # source_element_ids field, so a model without tool support cannot
        # participate at all (§5.5).
        if "TOOL" not in (model.get("inputModalities") or []) and not model.get(
            "responseStreamingSupported", True
        ):
            continue
        if "ON_DEMAND" not in (model.get("inferenceTypesSupported") or ["ON_DEMAND"]):
            # Only reachable through a profile, which is already listed above.
            continue
        candidates.append(
            {
                "id": model["modelId"],
                "name": model.get("modelName", ""),
                "kind": "foundation-model",
            }
        )

    return candidates


def choose(candidates: list[dict], preference: tuple[str, ...]) -> dict | None:
    """First candidate matching the highest-ranked preference."""
    for wanted in preference:
        for candidate in candidates:
            if wanted in candidate["id"]:
                return candidate
    return None


def put(region: str, name: str, value: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"    would write {name} = {value}")
        return
    boto3.client("ssm", region_name=region).put_parameter(
        Name=name, Value=value, Type="String", Overwrite=True
    )
    print(f"    wrote {name} = {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pin Bedrock model IDs in SSM (C5)")
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", help="show every candidate and exit")
    args = parser.parse_args(argv)

    try:
        candidates = list_invocable(args.region)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        print(f"Bedrock discovery failed: {exc}", file=sys.stderr)
        if code in ("AccessDeniedException", "UnrecognizedClientException"):
            print(
                "\nThe usual cause is that model access has not been granted. That is a "
                "manual step AWS requires and Terraform cannot do it — see the Bedrock "
                "section of docs/aws-setup.md.",
                file=sys.stderr,
            )
        return 1

    if not candidates:
        print(
            "No invocable Anthropic models in this account and region.\n"
            "Grant model access in the Bedrock console (docs/aws-setup.md), then re-run.",
            file=sys.stderr,
        )
        return 1

    if args.list:
        for candidate in candidates:
            print(f"  {candidate['kind']:<20} {candidate['id']}")
        return 0

    premium = choose(candidates, PREMIUM_PREFERENCE)
    cheap = choose(candidates, CHEAP_PREFERENCE)

    if premium is None or cheap is None:
        print("Could not resolve both models.", file=sys.stderr)
        print(f"  extraction (premium): {premium['id'] if premium else 'NOT FOUND'}", file=sys.stderr)
        print(f"  locate (cheap):       {cheap['id'] if cheap else 'NOT FOUND'}", file=sys.stderr)
        print("\nAvailable:", file=sys.stderr)
        for candidate in candidates:
            print(f"  {candidate['id']}", file=sys.stderr)
        # Deliberately not falling back to "whatever is available". A wrong model
        # silently changes what produced a quote.
        return 1

    prefix = f"/cbc-copilot/{args.environment}"
    print(f"\nResolved in {args.region}:")
    print(f"  extraction  {premium['id']}  ({premium['kind']})")
    print(f"  locate      {cheap['id']}  ({cheap['kind']})")
    print(f"\nPinning under {prefix}:")

    put(args.region, f"{prefix}/BEDROCK_MODEL_ID", premium["id"], dry_run=args.dry_run)
    put(args.region, f"{prefix}/BEDROCK_MODEL_ID_CHEAP", cheap["id"], dry_run=args.dry_run)

    print(
        "\nRestart the API and worker to pick these up — config is read once at "
        "process start.\nEvery extraction_runs row now records exactly which model "
        "produced its values."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
