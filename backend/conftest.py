"""
Root pytest configuration.

Sets the minimum environment a host-side test run needs BEFORE Django or
``shared.config`` is imported. shared/config.py deliberately raises on a missing
required variable (§8.4), so tests must supply them rather than relying on a
fallback that production would never have.

The database port is 55432 because the compose stack publishes Postgres there —
5432 is already claimed by a native install on some dev machines and Docker
silently loses that race.
"""

import os

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:password@127.0.0.1:55432/cbccopilot"
)
os.environ.setdefault("S3_SOURCE_BUCKET", "cbc-copilot-source-test")
os.environ.setdefault("S3_DERIVED_BUCKET", "cbc-copilot-derived-test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("LOG_FORMAT", "plain")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
