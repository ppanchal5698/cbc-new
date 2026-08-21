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
#: substring against inference-profile ids.
#:
#: This list is a generation ladder and it goes stale — that is inherent, not a
#: flaw to engineer away. What matters is that it stays ordered newest-first:
#: an entry like "claude-opus-4" also matches "claude-opus-4-8", so a stale list
#: silently pins an older model while a newer one sits enabled in the account,
#: and nothing fails. Add new families at the top when they appear.
PREMIUM_PREFERENCE = (
    "anthropic.claude-opus-5",
    "anthropic.claude-sonnet-5",
    "anthropic.claude-opus-4-8",
    "anthropic.claude-opus-4-7",
    "anthropic.claude-opus-4-6",
    "anthropic.claude-opus-4-5",
    "anthropic.claude-opus-4",
    "anthropic.claude-sonnet-4-6",
    "anthropic.claude-sonnet-4-5",
    "anthropic.claude-sonnet-4",
    "anthropic.claude-3-7-sonnet",
    "anthropic.claude-3-5-sonnet",
)

#: Preference order for the cheap locate pass.
CHEAP_PREFERENCE = (
    "anthropic.claude-haiku-4-5",
    "anthropic.claude-haiku-4",
    "anthropic.claude-3-5-haiku",
    "anthropic.claude-3-haiku",
)

#: Inference-profile prefixes, most-preferred first.
#:
#: NFR-4 is the reason this exists. A ``global.`` profile routes the request to
#: whichever region has capacity, anywhere in the world; a ``us.`` profile keeps
#: it inside the United States. Bid documents are client drawings, and "the model
#: ran somewhere in Europe that day" is not an answer anyone wants to give about
#: them. Both profiles serve the identical model, so preferring ``us.`` costs
#: nothing and settles the question.
REGION_SCOPE_PREFERENCE = ("us.", "apac.", "eu.", "global.")


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


#: Errors meaning "this account cannot use this model". Everything else —
#: throttling above all — means "ask again", and conflating the two reports a
#: perfectly good model as unavailable, non-deterministically.
DISQUALIFYING = {
    "AccessDeniedException",
    "ResourceNotFoundException",
    "ValidationException",
}


def is_invocable(model_id: str, region: str, *, attempts: int = 4) -> bool:
    """
    Can this account actually **call** this model?

    ``ListInferenceProfiles`` returns every profile that exists in the region, not
    the ones you have been granted. Those are different sets, and the difference
    stays invisible until the first real extraction call fails — precisely the
    failure C5 exists to prevent, since a pinned-but-uninvocable model ID is a
    deploy that looks configured and is not.

    The check is a one-token ``converse``. A denied call bills nothing and a
    successful one costs a fraction of a cent, so proving the grant is far cheaper
    than discovering its absence part-way through a 200-page bid set.

    Throttling is retried rather than counted as a refusal. Bedrock throttles a
    burst of these readily, and an unretried throttle marks a granted model
    unavailable — differently on each run, which is the worst way to be wrong.
    """
    import time

    from botocore.config import Config

    runtime = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"max_attempts": 1}, read_timeout=30),
    )

    for attempt in range(attempts):
        try:
            runtime.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
                inferenceConfig={"maxTokens": 1, "temperature": 0},
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in DISQUALIFYING:
                return False
            if attempt == attempts - 1:
                print(f"    ! {model_id}: {code} after {attempts} attempts", file=sys.stderr)
                return False
            time.sleep(2**attempt)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {model_id}: {type(exc).__name__}", file=sys.stderr)
            return False
    return False


def _scope_rank(candidate: dict) -> int:
    """Lower is better. Unprefixed foundation models sort after every profile."""
    for rank, prefix in enumerate(REGION_SCOPE_PREFERENCE):
        if candidate["id"].startswith(prefix):
            return rank
    return len(REGION_SCOPE_PREFERENCE)


def choose(
    candidates: list[dict],
    preference: tuple[str, ...],
    *,
    region: str | None = None,
    verify: bool = True,
) -> dict | None:
    """
    Best candidate: highest-ranked model family, then most-preferred region scope,
    then — unless disabled — the first one this account can actually invoke.

    All three orderings matter. The family decides capability; the region scope
    decides where client drawings are processed (NFR-4); invocability decides
    whether the pinned ID works at all.

    Verification walks down the preference order lazily, so a typical resolve costs
    one or two calls rather than probing the whole catalogue. That is not only
    faster: bulk-probing every model in a region reliably trips Bedrock's throttle,
    and a throttled probe is indistinguishable from a missing grant.
    """
    for wanted in preference:
        for candidate in sorted(
            (c for c in candidates if wanted in c["id"]), key=_scope_rank
        ):
            if not verify or region is None:
                return candidate
            print(f"    trying {candidate['id']}")
            if is_invocable(candidate["id"], region):
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
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the invocability check (faster, but can pin an ungranted model)",
    )
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

    verify = not args.no_verify
    if verify:
        print("Resolving against models this account can actually invoke:")
    premium = choose(candidates, PREMIUM_PREFERENCE, region=args.region, verify=verify)
    cheap = choose(candidates, CHEAP_PREFERENCE, region=args.region, verify=verify)

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
