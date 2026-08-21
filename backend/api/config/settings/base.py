"""
Django settings shared by every environment.

Configuration is read through ``shared.config.get_settings()`` so both services
resolve the same variables the same way, with the same precedence, and fail at
start-up on a missing required value (§8.4). Nothing here invents a fallback.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/api
REPO_BACKEND = BASE_DIR.parent                            # backend/

# ``shared`` is imported by both services and lives one level above the Django
# project root. Added here, once, rather than by sys.path stanzas in every model
# module (which is what the previous layout did).
if str(REPO_BACKEND) not in sys.path:
    sys.path.insert(0, str(REPO_BACKEND))

from shared.config import PROD, get_settings  # noqa: E402

settings_obj = get_settings()

ENVIRONMENT = settings_obj.environment


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

# No insecure default outside local: a shipped SECRET_KEY is a forged-session
# vulnerability, and §8.4 forbids silently defaulting a required variable.
if settings_obj.is_local:
    SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-local-only-do-not-deploy")
else:
    from shared.config import env_str

    SECRET_KEY = env_str("SECRET_KEY")

DEBUG = settings_obj.is_local and os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    # Provides the Token model that REST_FRAMEWORK's TokenAuthentication needs.
    # Listing that auth class without this app means the class is configured and
    # can never authenticate anyone, because the table it reads does not exist.
    "rest_framework.authtoken",
    "drf_spectacular",
    "django_filters",
    # Django owns every migration in this project (§3.2 rule 1, ADR-0001).
    # The FastAPI worker uses SQLAlchemy Core against these tables and never
    # runs a migration of its own.
    "authentication",
    "common",
    "projects",
    "openings",
    "catalog",
    "pricing",
    "quotes",
    "feedback",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

APPEND_SLASH = False

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Postgres 17 only (D2). There is no SQLite fallback: the provenance model
# assumes native uuid, numeric, and JSONB, and a fallback engine produces a
# database that passes tests and can never hold a real bid set.

from shared import db_url  # noqa: E402

DATABASES = {
    "default": db_url.parse(
        settings_obj.database_url,
        # CONN_MAX_AGE must be 0 behind PgBouncer in transaction mode: persistent
        # Django connections defeat the pooler and reintroduce bottleneck B10.
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "0")),
        connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
        statement_timeout_ms=int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000")),
    )
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
# Django auth is the authorisation and audit boundary (C3 / ADR-0004). Cognito is
# deferred; if SSO is later required it sits in FRONT of Django as an OIDC
# provider and Django still owns permissions and the audit trail.

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# i18n / static
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)


# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    # Authenticated by default. Every endpoint touches customer drawings or
    # pricing (NFR-4, §11.2); an endpoint that wants to be public must opt out
    # explicitly and say why.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # doc_elements runs to tens of thousands of rows per bid set (Risk R9).
    # An unpaginated list endpoint over that table is a denial of service against
    # our own API host.
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardPagination",
    "PAGE_SIZE": 100,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CBC Estimating & Pricing Copilot API",
    "DESCRIPTION": (
        "Internal API for the CBC estimating copilot. The estimator stays in "
        "control of every quote: this API drafts, sources, and calculates — it "
        "does not send."
    ),
    "VERSION": "3.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # The frontend generates its types from this schema (§8.2, fixes H2).
    "SCHEMA_PATH_PREFIX": "/api",
    "ENUM_NAME_OVERRIDES": {
        "ReviewStateEnum": "shared.enums.ReviewState.choices",
        "CostSourceEnum": "shared.enums.CostSource.choices",
    },
}


# ---------------------------------------------------------------------------
# Logging (§11.5)
# ---------------------------------------------------------------------------
# JSON to stdout, picked up by the CloudWatch agent. Correlation ID is the
# pipeline_job_id, injected by the worker; the API logs request_id.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
        "plain": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if settings_obj.log_format == "json" else "plain",
        },
    },
    "root": {"handlers": ["console"], "level": settings_obj.log_level},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "cbc": {"level": settings_obj.log_level, "propagate": True},
    },
}


# ---------------------------------------------------------------------------
# Production hardening
# ---------------------------------------------------------------------------

if ENVIRONMENT == PROD:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"
    SECURE_CONTENT_TYPE_NOSNIFF = True
