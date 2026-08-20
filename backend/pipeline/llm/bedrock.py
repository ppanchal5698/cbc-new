"""
Claude on Amazon Bedrock (§3.1, §5.4, §5.12, C5/D12).

**The model ID is never hardcoded.** Architecture v2 wrote
``anthropic.claude-opus-5``, which is not a resolvable Bedrock model or
inference-profile ID. The resolution rule (C5) is:

    Resolve at deploy time via ``bedrock:ListFoundationModels`` /
    ``ListInferenceProfiles``, pin the resolved identifier in SSM Parameter
    Store, and record the resolved value on every ``extraction_runs`` row so a
    re-run is attributable to an exact model version.

This module reads the pinned value and refuses to invent one. :mod:`ops.scripts.resolve_bedrock_models`
does the resolving, once, at deploy.

**Data residency (NFR-4).** Bedrock runs in the same AWS account and region as S3
and RDS, so drawings never leave the account. The NIM escape hatch below is
reachable only when ``ENVIRONMENT=local``; ``shared.config`` raises otherwise, and
that is deliberately the *only* place the rule is enforced so it cannot be
bypassed by importing something else (open item Q2).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from shared.config import get_settings

log = logging.getLogger("cbc.bedrock")

#: Throttling is the common failure and it is transient. Bounded, not infinite —
#: an extraction that retries forever is an extraction nobody can cost.
MAX_ATTEMPTS = 5
BASE_BACKOFF = 1.5

#: Prompt caching requires a prefix of at least this many tokens to be cacheable.
#: Below it, a cache point is silently ignored and the 1.25x write cost is paid
#: for nothing (§5.12).
MIN_CACHEABLE_TOKENS = 1024

#: Rough characters-per-token, used only to warn when a prefix is too short to
#: cache. Never used for billing or for budget decisions.
CHARS_PER_TOKEN = 3.6


class BedrockError(RuntimeError):
    """The model call failed, or returned something the contract forbids."""


class ToolNotCalled(BedrockError):
    """
    The model answered in prose instead of calling the tool.

    §5.4 enforces structure with tool use so free text is impossible. If this is
    raised the schema is the contract and prose is not accepted — it is a
    rejection, not something to parse.
    """


@dataclass
class LLMResponse:
    """One structured model response, with the accounting §5.12 requires."""

    payload: dict
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = ""
    latency_ms: int = 0
    raw_text: str = field(default="", repr=False)


def _client():
    settings_obj = get_settings()
    return boto3.client(
        "bedrock-runtime",
        config=Config(
            # Bedrock's own retries are disabled: this module implements its own
            # so that every attempt is logged and counted against MAX_ATTEMPTS.
            # Two retry layers multiply, and the product is not a number anyone
            # budgeted for.
            retries={"max_attempts": 1, "mode": "standard"},
            read_timeout=300,
            connect_timeout=10,
        ),
        **settings_obj.boto_kwargs,
    )


# ---------------------------------------------------------------------------
# Model resolution (C5)
# ---------------------------------------------------------------------------

def resolve_models(region: str | None = None) -> dict[str, list[str]]:
    """
    List what this account can actually invoke, for the deploy-time resolver.

    Inference profiles are listed first and preferred: cross-region profiles
    (``us.anthropic.*``) are what most accounts are actually granted, and a bare
    foundation-model ID often fails with an on-demand-throughput error.
    """
    settings_obj = get_settings()
    client = boto3.client("bedrock", region_name=region or settings_obj.aws_region)

    profiles: list[str] = []
    try:
        paginator = client.get_paginator("list_inference_profiles")
        for page in paginator.paginate():
            profiles.extend(
                p["inferenceProfileId"]
                for p in page.get("inferenceProfileSummaries", [])
                if "anthropic" in p.get("inferenceProfileId", "").lower()
            )
    except ClientError as exc:
        log.warning("could not list inference profiles: %s", exc)

    models: list[str] = []
    try:
        response = client.list_foundation_models(byProvider="anthropic")
        models = [
            m["modelId"]
            for m in response.get("modelSummaries", [])
            if "ON_DEMAND" in m.get("inferenceTypesSupported", [])
        ]
    except ClientError as exc:
        log.warning("could not list foundation models: %s", exc)

    return {"inference_profiles": sorted(profiles), "foundation_models": sorted(models)}


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def build_messages(*, cacheable_prefix: str, variable_body: str) -> tuple[list[dict], list[dict]]:
    """
    Split the prompt into a cacheable system prefix and a variable user body.

    Prompt caching bills cached input tokens at roughly a tenth of the standard
    rate, and the cache write costs about 1.25x — so a prefix reused twice within
    the TTL is already ahead (§5.12). Three constraints make it work, and all
    three are the caller's responsibility to honour:

    * The prefix must be **>= 1,024 tokens** to be cacheable at all.
    * It must be **byte-identical** across calls. Never interpolate a document ID,
      a timestamp, or a run ID into it.
    * It must come **first**.

    The static content here is the system prompt, the finish-code table, and the
    few-shot examples — identical for every table in every bid set.
    """
    estimated = _estimate_tokens(cacheable_prefix)
    system: list[dict] = [{"text": cacheable_prefix}]
    if estimated >= MIN_CACHEABLE_TOKENS:
        system.append({"cachePoint": {"type": "default"}})
    else:
        log.debug(
            "prefix is ~%d tokens, below the %d-token cache floor; no cache point set",
            estimated,
            MIN_CACHEABLE_TOKENS,
        )

    messages = [{"role": "user", "content": [{"text": variable_body}]}]
    return system, messages


def invoke_tool(
    *,
    model_id: str,
    system: list[dict],
    messages: list[dict],
    tool_spec: dict,
    tool_name: str,
    max_tokens: int | None = None,
) -> LLMResponse:
    """
    Call Bedrock Converse and require a tool call back.

    Inference parameters are fixed, not tuned per call: ``temperature=0`` with a
    fixed ``top_p`` and ``max_tokens`` is what makes an extraction reproducible,
    and an extraction that cannot be reproduced cannot be audited (§5.4).
    """
    settings_obj = get_settings()
    client = _client()

    request = {
        "modelId": model_id,
        "system": system,
        "messages": messages,
        "inferenceConfig": {
            "maxTokens": max_tokens or settings_obj.bedrock_max_tokens,
            "temperature": settings_obj.bedrock_temperature,
            "topP": settings_obj.bedrock_top_p,
        },
        "toolConfig": {
            "tools": [{"toolSpec": tool_spec}],
            # Forced, not "auto": §5.4 enforces structure so free text is
            # impossible. A model that could choose prose would eventually choose
            # it on the one malformed table that mattered.
            "toolChoice": {"tool": {"name": tool_name}},
        },
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            response = client.converse(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "ModelTimeoutException", "ServiceUnavailableException"):
                last_error = exc
                sleep_for = BASE_BACKOFF**attempt
                log.warning(
                    "bedrock %s on attempt %d/%d; backing off %.1fs",
                    code, attempt, MAX_ATTEMPTS, sleep_for,
                )
                time.sleep(sleep_for)
                continue
            if code == "AccessDeniedException":
                raise BedrockError(
                    f"access denied invoking {model_id!r}. Bedrock model access is a "
                    f"manual console grant per model per region — see docs/aws-setup.md "
                    f"and open item Q1."
                ) from exc
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = response.get("usage", {})
        content = response.get("output", {}).get("message", {}).get("content", [])

        tool_use = next((block["toolUse"] for block in content if "toolUse" in block), None)
        if tool_use is None:
            text = " ".join(block.get("text", "") for block in content)
            raise ToolNotCalled(
                f"model returned prose instead of calling {tool_name!r} "
                f"(stop reason {response.get('stopReason')}): {text[:200]}"
            )

        return LLMResponse(
            payload=tool_use.get("input", {}),
            model_id=model_id,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cache_read_tokens=usage.get("cacheReadInputTokens", 0),
            cache_write_tokens=usage.get("cacheWriteInputTokens", 0),
            stop_reason=response.get("stopReason", ""),
            latency_ms=latency_ms,
        )

    raise BedrockError(f"bedrock failed after {MAX_ATTEMPTS} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Local escape hatch (§11.1, open item Q2)
# ---------------------------------------------------------------------------

def invoke_tool_local(
    *, system: list[dict], messages: list[dict], tool_spec: dict, tool_name: str
) -> LLMResponse:
    """
    The same contract against an OpenAI-compatible local endpoint.

    **Local development only.** ``Settings.require_local_llm`` raises outside
    ``ENVIRONMENT=local``, which is what stops this becoming the accidental second
    extraction path open item Q2 warns about. It exists so the pipeline can be
    developed without Bedrock access or spend — not as a fallback when Bedrock is
    unavailable, because a silent provider switch would break the model
    attribution NFR-3 requires.
    """
    settings_obj = get_settings()
    base_url, api_key, model = settings_obj.require_local_llm()

    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
    prompt = "\n\n".join(block["text"] for block in system if "text" in block)
    body = "\n\n".join(
        block.get("text", "") for message in messages for block in message.get("content", [])
    )

    started = time.perf_counter()
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": body}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": tool_spec["name"],
                    "description": tool_spec.get("description", ""),
                    "parameters": tool_spec["inputSchema"]["json"],
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": tool_name}},
        temperature=settings_obj.bedrock_temperature,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    choice = completion.choices[0]
    if not choice.message.tool_calls:
        raise ToolNotCalled(f"local model did not call {tool_name!r}")

    usage = completion.usage
    return LLMResponse(
        payload=json.loads(choice.message.tool_calls[0].function.arguments),
        # Prefixed so an extraction_runs row can never be mistaken for a Bedrock
        # run when someone audits which model produced a quote (NFR-3).
        model_id=f"LOCAL:{model}",
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        stop_reason=choice.finish_reason or "",
        latency_ms=latency_ms,
    )


def invoke(
    *, model_id: str, system: list[dict], messages: list[dict], tool_spec: dict, tool_name: str
) -> LLMResponse:
    """Dispatch to Bedrock, or to the local hatch when one is configured."""
    settings_obj = get_settings()
    if settings_obj.is_local and settings_obj.nim_api_key:
        return invoke_tool_local(
            system=system, messages=messages, tool_spec=tool_spec, tool_name=tool_name
        )
    return invoke_tool(
        model_id=model_id,
        system=system,
        messages=messages,
        tool_spec=tool_spec,
        tool_name=tool_name,
    )
