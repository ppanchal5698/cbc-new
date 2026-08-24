"""
The single configuration object for both services (§8.4).

**Precedence:** process env → SSM Parameter Store (staging/prod) → ``.env`` (local
only) → defaults declared here.

**The application fails to start on a missing required variable — never silently
defaults.** That rule is the whole reason this module exists rather than scattered
``os.environ.get(..., "some-fallback")`` calls: a fallback bucket name or a
fallback model ID is a wrong answer that looks like a working system.

Nothing here reads a secret from a file in production. In staging and prod the
SecureString values are pulled from SSM at start-up and never written to disk.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------

LOCAL = "local"
DEV = "dev"
STAGING = "staging"
PROD = "prod"
ENVIRONMENTS = (LOCAL, DEV, STAGING, PROD)

#: Environments whose secrets come from SSM rather than a .env file.
SSM_ENVIRONMENTS = (STAGING, PROD)

#: Services MiniStack emulates locally, and therefore the only ones
#: ``AWS_ENDPOINT_URL`` may be applied to. Must match ``MINISTACK_SERVICES`` in
#: docker-compose.yml — a service listed here but not there resolves to an
#: endpoint that refuses the request; a service there but not here quietly talks
#: to real AWS from a developer machine.
#:
#: Bedrock and Textract are deliberately absent: neither is emulated, and both
#: have their own offline story (§11.1's NIM hatch, and ``FAKE_OCR=1``).
EMULATED_SERVICES = frozenset({"s3", "sqs", "sns", "ssm"})


class ConfigError(RuntimeError):
    """A required variable is missing, unparseable, or forbidden in this environment."""


_MISSING = object()


# ---------------------------------------------------------------------------
# Primitive readers
# ---------------------------------------------------------------------------

def _raw(name: str, default: Any = _MISSING) -> Any:
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    if default is _MISSING:
        raise ConfigError(
            f"required configuration variable {name!r} is not set. "
            f"See .env.example and docs/aws-setup.md; §8.4 forbids a silent default."
        )
    return default


def env_str(name: str, default: Any = _MISSING) -> str:
    value = _raw(name, default)
    return str(value) if value is not None else None  # type: ignore[return-value]


def env_int(name: str, default: Any = _MISSING) -> int:
    value = _raw(name, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name}={value!r} is not an integer") from exc


def env_float(name: str, default: Any = _MISSING) -> float:
    value = _raw(name, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name}={value!r} is not a number") from exc


def env_decimal(name: str, default: Any = _MISSING) -> Decimal:
    """Money and cost guards use Decimal. Never float — see §6.2 'to the cent'."""
    value = _raw(name, default)
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - Decimal raises several types
        raise ConfigError(f"{name}={value!r} is not a decimal") from exc


def env_bool(name: str, default: Any = _MISSING) -> bool:
    value = _raw(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: Any = _MISSING, sep: str = ",") -> list[str]:
    value = _raw(name, default)
    if isinstance(value, list):
        return value
    return [item.strip() for item in str(value).split(sep) if item.strip()]


# ---------------------------------------------------------------------------
# .env tier (local only)
# ---------------------------------------------------------------------------

def _find_dotenv() -> Path | None:
    """Walk up from this file looking for a repo-root ``.env``."""
    from pathlib import Path

    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(path: Path | None = None) -> int:
    """
    Load ``.env`` into ``os.environ`` without overwriting anything already set.

    This is the third tier of the §8.4 precedence chain — *process env -> SSM ->
    .env (local only) -> defaults* — and it was previously unimplemented, so any
    process started outside Docker (a host-side pytest run, a management command,
    an ops script) saw none of the project's configuration and died on the first
    required variable.

    Parsed with the standard library rather than a dependency: the format is
    ``KEY=value`` with ``#`` comments, and quoted values unwrapped. Nothing fancy,
    because a .env file that needs fancy parsing is a configuration smell.

    **Local only.** Staging and production read SecureStrings from SSM and never
    read a secret from a file (§8.4).
    """
    target = path or _find_dotenv()
    if target is None or not Path(target).is_file():
        return 0

    loaded = 0
    for line in Path(target).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # Process env wins: an explicit export must beat the file.
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


# ---------------------------------------------------------------------------
# SSM hydration
# ---------------------------------------------------------------------------

def hydrate_from_ssm(prefix: str, region: str) -> int:
    """
    Load every parameter under ``prefix`` into ``os.environ``.

    Process env wins: a variable already set is left alone, so an operator can
    override one value without editing the parameter store. Returns the number of
    parameters injected.

    Called once at start-up by :func:`get_settings` for staging and prod. Import
    of boto3 is deferred so local development and unit tests never pay for it.
    """
    import boto3

    client = boto3.client("ssm", region_name=region)
    paginator = client.get_paginator("get_parameters_by_path")
    injected = 0
    for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
        for param in page.get("Parameters", []):
            name = param["Name"].rsplit("/", 1)[-1].upper()
            if os.environ.get(name):
                continue  # process env wins
            os.environ[name] = param["Value"]
            injected += 1
    return injected


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Every §8.4 variable, typed and validated once at start-up."""

    # -- environment ------------------------------------------------------
    environment: str
    log_level: str
    log_format: str

    # -- database ---------------------------------------------------------
    database_url: str

    # -- AWS --------------------------------------------------------------
    aws_region: str
    #: MiniStack endpoint for local emulation. None in every real environment.
    aws_endpoint_url: str | None
    s3_source_bucket: str
    s3_derived_bucket: str
    cloudfront_domain: str | None
    #: Browser-facing origin for page rasters, when it differs from the endpoint
    #: the API itself uses. Local only: inside compose the emulator is
    #: ``ministack:4566``, a name no browser can resolve. CloudFront covers this
    #: everywhere else, so it stays unset in staging and production.
    public_raster_endpoint_url: str | None

    # -- queues (§3.1, C6) ------------------------------------------------
    document_ready_queue: str
    document_ready_dlq: str
    ocr_complete_queue: str
    textract_sns_topic_arn: str | None
    textract_sns_role_arn: str | None
    sqs_visibility_timeout_seconds: int
    sqs_max_receive_count: int

    # -- Bedrock (C5 / D12) ----------------------------------------------
    bedrock_model_id: str | None
    bedrock_model_id_cheap: str | None
    extraction_prompt_version: str
    locate_prompt_version: str
    hardware_prompt_version: str
    max_extract_tables_per_document: int
    bedrock_max_tokens: int
    bedrock_temperature: float
    bedrock_top_p: float

    # -- extraction thresholds (§5.9, C14) --------------------------------
    confidence_threshold_default: float
    confidence_threshold_fire_rating: float
    confidence_threshold_handing: float
    grounding_min_ratio: int

    # -- preprocessing (§4) ----------------------------------------------
    ocr_route_config: str
    max_ocr_cost_per_document_usd: Decimal
    raster_max_long_edge_px: int

    # -- matching (§6.1) -------------------------------------------------
    match_top_n: int
    match_confidence_cutoff: float
    match_size_tolerance_inches: int

    # -- pricing (§6.2) --------------------------------------------------
    cost_freshness_months: int

    #: Replay OCR from the PDF's own text layer instead of calling Textract
    #: (§8.3). Makes the whole pipeline runnable offline with no AWS calls and no
    #: spend. Refused outside local — a production run that silently skipped OCR
    #: would produce a confident empty extraction.
    fake_ocr: bool = False

    # -- local-only escape hatch (§11.1, decision D-a) --------------------
    nim_base_url: str | None = None
    nim_api_key: str | None = None
    nim_chat_model: str | None = None

    _ssm_parameters_loaded: int = field(default=0, repr=False)

    # -- derived properties ----------------------------------------------

    @property
    def is_local(self) -> bool:
        return self.environment == LOCAL

    @property
    def is_production(self) -> bool:
        return self.environment == PROD

    def boto_kwargs_for(self, service: str) -> dict[str, Any]:
        """
        boto3 client kwargs for one service.

        ``endpoint_url`` is only ever populated for local MiniStack, and **only
        for the services MiniStack actually emulates**. In AWS the default
        resolver is used, which is what lets the free S3 gateway endpoint
        (§10.3 item 4) take effect without any client-side configuration.

        The per-service scoping is load-bearing. Applying the override to every
        client sends Bedrock to an emulator that has never implemented it, so a
        local extraction call fails at an endpoint that cannot answer — with an
        error that looks like a Bedrock problem rather than a configuration one.
        Bedrock has no local substitute at all; §11.1's NIM hatch is the
        alternative, and it is reached through a different code path entirely.

        Textract likewise is not emulated, so it resolves to real AWS. That is
        correct and deliberate: the offline substitute is ``FAKE_OCR=1``, and the
        spend control is ``MAX_OCR_COST_PER_DOCUMENT_USD``, which is checked
        before any OCR call rather than by making the client unusable.
        """
        kwargs: dict[str, Any] = {"region_name": self.aws_region}
        if self.aws_endpoint_url and service in EMULATED_SERVICES:
            kwargs["endpoint_url"] = self.aws_endpoint_url
        return kwargs

    def require_bedrock(self) -> tuple[str, str]:
        """
        Return ``(premium_model_id, cheap_model_id)`` or explain why we cannot.

        Model IDs are resolved at deploy time and pinned in SSM (C5): a hardcoded
        ``anthropic.claude-opus-5`` is not a resolvable identifier, and a run that
        cannot name its exact model version cannot be audited (NFR-3).
        """
        if not self.bedrock_model_id or not self.bedrock_model_id_cheap:
            raise ConfigError(
                "BEDROCK_MODEL_ID and BEDROCK_MODEL_ID_CHEAP must be resolved at deploy "
                "time and pinned in SSM (§8.4, C5). Run 'make bedrock-resolve' or see "
                "docs/aws-setup.md. They are deliberately not defaulted."
            )
        return self.bedrock_model_id, self.bedrock_model_id_cheap

    def require_local_llm(self) -> tuple[str, str, str]:
        """
        Return NIM connection details, or refuse.

        §11.1: "Direct Anthropic API access exists only as a configuration escape
        hatch, disabled in production, and must never become a second extraction
        path." Open item Q2 says the same of NVIDIA NIM. This method is the
        enforcement point — outside ``local`` there is no way to obtain a client.
        """
        if self.environment != LOCAL:
            raise ConfigError(
                f"the NIM escape hatch is local-only and ENVIRONMENT={self.environment!r}. "
                "Bedrock is the sole extraction path outside local development "
                "(§11.1, open item Q2)."
            )
        if not (self.nim_base_url and self.nim_api_key and self.nim_chat_model):
            raise ConfigError(
                "NIM_BASE_URL, NIM_API_KEY and NIM_CHAT_MODEL must all be set to use "
                "the local LLM escape hatch."
            )
        return self.nim_base_url, self.nim_api_key, self.nim_chat_model


def _reject_global_endpoint_override() -> None:
    """
    Refuse ``AWS_ENDPOINT_URL`` and its per-service siblings.

    botocore reads these directly from the environment and applies them **beneath**
    any ``endpoint_url`` we pass, and ``AWS_ENDPOINT_URL`` applies to *every*
    service. So setting it does not merely configure S3 and SQS — it silently
    redirects Bedrock and Textract too.

    That failure is close to undetectable. MiniStack answers a Bedrock call with a
    mock completion rather than an error, so extraction "succeeds", every table
    comes back classified the same way, and the pipeline degrades to its
    extract-everything fallback. The bill and the eval numbers both look like a
    model problem.

    The local emulator endpoint therefore travels under a name botocore does not
    recognise, and this guard exists so that anyone setting the standard variable
    out of habit is told exactly why it is wrong instead of discovering it in a
    quality report.
    """
    offenders = sorted(
        name
        for name in os.environ
        if name == "AWS_ENDPOINT_URL" or name.startswith("AWS_ENDPOINT_URL_")
        if os.environ[name].strip()
    )
    if not offenders:
        return
    raise ConfigError(
        f"{', '.join(offenders)} is set. botocore applies these to every service, "
        "including Bedrock and Textract, which have no local emulator — MiniStack "
        "answers a redirected Bedrock call with a mock instead of failing, so the "
        "damage is silent. Use LOCAL_AWS_ENDPOINT_URL instead; shared/config.py "
        "applies it only to the services in EMULATED_SERVICES."
    )


def _build() -> Settings:
    # Tier 3 of the precedence chain, before anything is read. Skipped entirely
    # once ENVIRONMENT names a deployed environment, so a stray .env on a server
    # can never shadow SSM.
    if os.environ.get("ENVIRONMENT", LOCAL).strip().lower() in (LOCAL, DEV):
        load_dotenv()

    environment = env_str("ENVIRONMENT", LOCAL).strip().lower()
    if environment not in ENVIRONMENTS:
        raise ConfigError(f"ENVIRONMENT={environment!r} must be one of {ENVIRONMENTS}")

    aws_region = env_str("AWS_REGION", env_str("AWS_DEFAULT_REGION", "us-east-1"))

    injected = 0
    ssm_prefix = os.environ.get("CBC_SSM_PREFIX")
    if environment in SSM_ENVIRONMENTS:
        if not ssm_prefix:
            raise ConfigError(
                f"ENVIRONMENT={environment} requires CBC_SSM_PREFIX "
                "(e.g. /cbc-copilot/prod/). Secrets are never read from a file here."
            )
        injected = hydrate_from_ssm(ssm_prefix, aws_region)
    elif ssm_prefix:
        injected = hydrate_from_ssm(ssm_prefix, aws_region)

    settings = Settings(
        environment=environment,
        log_level=env_str("LOG_LEVEL", "INFO").upper(),
        log_format=env_str("LOG_FORMAT", "json").lower(),
        # No fallback: §8.4, and the SQLite fallback this replaces silently
        # produced a database that passed tests and could never hold a bid set.
        database_url=env_str("DATABASE_URL"),
        aws_region=aws_region,
        # LOCAL_AWS_ENDPOINT_URL, deliberately not AWS_ENDPOINT_URL. See
        # _reject_global_endpoint_override below for why the standard name is
        # unusable here.
        aws_endpoint_url=env_str("LOCAL_AWS_ENDPOINT_URL", None),
        s3_source_bucket=env_str("S3_SOURCE_BUCKET"),
        s3_derived_bucket=env_str("S3_DERIVED_BUCKET"),
        cloudfront_domain=env_str("CLOUDFRONT_DOMAIN", None),
        public_raster_endpoint_url=env_str("PUBLIC_RASTER_ENDPOINT_URL", None),
        document_ready_queue=env_str("DOCUMENT_READY_QUEUE", "document-ready"),
        document_ready_dlq=env_str("DOCUMENT_READY_DLQ", "document-ready-dlq"),
        ocr_complete_queue=env_str("OCR_COMPLETE_QUEUE", "ocr-complete"),
        textract_sns_topic_arn=env_str("TEXTRACT_SNS_TOPIC_ARN", None),
        textract_sns_role_arn=env_str("TEXTRACT_SNS_ROLE_ARN", None),
        sqs_visibility_timeout_seconds=env_int("SQS_VISIBILITY_TIMEOUT_SECONDS", 900),
        sqs_max_receive_count=env_int("SQS_MAX_RECEIVE_COUNT", 3),
        # Deliberately None-able: resolved at deploy, never hardcoded (C5).
        bedrock_model_id=env_str("BEDROCK_MODEL_ID", None),
        bedrock_model_id_cheap=env_str("BEDROCK_MODEL_ID_CHEAP", None),
        extraction_prompt_version=env_str("EXTRACTION_PROMPT_VERSION", "v2"),
        locate_prompt_version=env_str("LOCATE_PROMPT_VERSION", "v1"),
        hardware_prompt_version=env_str("HARDWARE_PROMPT_VERSION", "v1"),
        max_extract_tables_per_document=env_int("MAX_EXTRACT_TABLES_PER_DOCUMENT", 40),
        bedrock_max_tokens=env_int("BEDROCK_MAX_TOKENS", 8192),
        # temperature=0 is a correctness requirement, not a tuning knob (§5.4):
        # an extraction that cannot be reproduced cannot be audited.
        bedrock_temperature=env_float("BEDROCK_TEMPERATURE", 0.0),
        bedrock_top_p=env_float("BEDROCK_TOP_P", 1.0),
        # 0.80 is a PLACEHOLDER until §5.9 calibration replaces it with a measured
        # value CBC chooses. It is configuration precisely so it is never a constant
        # in code (C14).
        confidence_threshold_default=env_float("CONFIDENCE_THRESHOLD_DEFAULT", 0.80),
        confidence_threshold_fire_rating=env_float("CONFIDENCE_THRESHOLD_FIRE_RATING", 0.95),
        confidence_threshold_handing=env_float("CONFIDENCE_THRESHOLD_HANDING", 0.95),
        grounding_min_ratio=env_int("GROUNDING_MIN_RATIO", 90),
        ocr_route_config=env_str("OCR_ROUTE_CONFIG", "config/ocr_routes.json"),
        max_ocr_cost_per_document_usd=env_decimal("MAX_OCR_COST_PER_DOCUMENT_USD", "2.00"),
        raster_max_long_edge_px=env_int("RASTER_MAX_LONG_EDGE_PX", 4000),
        # 3 mirrors the estimator behaviour CBC validated: "here are 3 close
        # matches - is it one of these?" (§6.1)
        match_top_n=env_int("MATCH_TOP_N", 3),
        # Below this, route to the manual/custom-RFQ path rather than
        # auto-proposing a line. The estimator owns the long tail by design, not
        # by failure (NR-13). Calibrate against the golden set like every other
        # threshold; the default is a starting point, not a measurement.
        match_confidence_cutoff=env_float("MATCH_CONFIDENCE_CUTOFF", 0.60),
        match_size_tolerance_inches=env_int("MATCH_SIZE_TOLERANCE_INCHES", 2),
        cost_freshness_months=env_int("COST_FRESHNESS_MONTHS", 8),
        fake_ocr=env_bool("FAKE_OCR", False),
        nim_base_url=env_str("NIM_BASE_URL", None),
        nim_api_key=env_str("NIM_API_KEY", None),
        nim_chat_model=env_str("NIM_CHAT_MODEL", None),
        _ssm_parameters_loaded=injected,
    )

    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    """Fail loudly at start-up rather than subtly at runtime."""
    for name, value in (
        ("CONFIDENCE_THRESHOLD_DEFAULT", s.confidence_threshold_default),
        ("CONFIDENCE_THRESHOLD_FIRE_RATING", s.confidence_threshold_fire_rating),
        ("CONFIDENCE_THRESHOLD_HANDING", s.confidence_threshold_handing),
    ):
        if not 0.0 <= value <= 1.0:
            raise ConfigError(f"{name}={value} must be within [0, 1]")

    # §5.8: rating and handing warrant a stricter threshold than everything else,
    # because their cost of error is categorically different. A configuration that
    # inverts that is a misconfiguration, not a preference.
    if s.confidence_threshold_fire_rating < s.confidence_threshold_default:
        raise ConfigError(
            "CONFIDENCE_THRESHOLD_FIRE_RATING must be >= CONFIDENCE_THRESHOLD_DEFAULT "
            "(§5.8: fire rating is a zero-tolerance field)"
        )
    if s.confidence_threshold_handing < s.confidence_threshold_default:
        raise ConfigError(
            "CONFIDENCE_THRESHOLD_HANDING must be >= CONFIDENCE_THRESHOLD_DEFAULT (§5.8)"
        )

    if not 0.0 <= s.match_confidence_cutoff <= 1.0:
        raise ConfigError(
            f"MATCH_CONFIDENCE_CUTOFF={s.match_confidence_cutoff} must be within [0, 1]"
        )
    if s.match_top_n < 1:
        raise ConfigError("MATCH_TOP_N must be >= 1")

    if not 0 <= s.grounding_min_ratio <= 100:
        raise ConfigError(f"GROUNDING_MIN_RATIO={s.grounding_min_ratio} must be within [0, 100]")

    if s.max_ocr_cost_per_document_usd <= 0:
        raise ConfigError(
            "MAX_OCR_COST_PER_DOCUMENT_USD must be positive — it is the only control "
            "that catches an accidental 3,000-page upload before the money is gone (§10.3)"
        )

    if s.max_extract_tables_per_document < 1:
        raise ConfigError(
            f"MAX_EXTRACT_TABLES_PER_DOCUMENT={s.max_extract_tables_per_document} must be >= 1"
        )

    if s.sqs_max_receive_count < 1:
        raise ConfigError("SQS_MAX_RECEIVE_COUNT must be >= 1 (C6 redrive policy)")

    if s.aws_endpoint_url and s.environment in (STAGING, PROD):
        raise ConfigError(
            f"LOCAL_AWS_ENDPOINT_URL is set in ENVIRONMENT={s.environment}. That points "
            "the SDK at a local emulator and would silently bypass every real AWS service."
        )

    _reject_global_endpoint_override()

    if s.fake_ocr and s.environment != LOCAL:
        raise ConfigError(
            f"FAKE_OCR is set in ENVIRONMENT={s.environment}. Replaying OCR outside "
            "local development would produce a confident, empty extraction with no "
            "signal that anything was skipped (§8.3, NFR-2)."
        )

    if s.environment != LOCAL and s.nim_api_key:
        raise ConfigError(
            f"NIM_API_KEY is set in ENVIRONMENT={s.environment}. The local LLM escape "
            "hatch must never be reachable outside local development (§11.1, Q2)."
        )

    if s.is_production and not s.textract_sns_topic_arn:
        raise ConfigError(
            "TEXTRACT_SNS_TOPIC_ARN is required in production: OCR completion arrives "
            "via SNS, never a polling loop (bottleneck B2)."
        )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (once) and return the process-wide settings object."""
    return _build()


def reset_settings_cache() -> None:
    """Drop the cached Settings. Tests only."""
    get_settings.cache_clear()


__all__ = [
    "LOCAL",
    "DEV",
    "STAGING",
    "PROD",
    "ENVIRONMENTS",
    "ConfigError",
    "Settings",
    "get_settings",
    "reset_settings_cache",
    "hydrate_from_ssm",
    "load_dotenv",
    "env_str",
    "env_int",
    "env_float",
    "env_bool",
    "env_list",
    "env_decimal",
]
