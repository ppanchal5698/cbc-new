"""
One parser for ``DATABASE_URL``, shared by both services.

Django wants a settings dict, psycopg wants a DSN string, and SQLAlchemy wants a
URL with a driver prefix. All three are derived here from the same input so the
two services can never end up pointed at different databases — which is exactly
what the previous inline regex in ``settings/base.py`` made possible, since the
pipeline was handed a ``postgresql+psycopg://`` URL the regex did not match.

Postgres only (D2). ``postgres://`` is accepted as an alias because that is what
Heroku-style tooling and several AWS consoles emit.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlparse

POSTGRES_SCHEMES = ("postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2")


class DatabaseUrlError(ValueError):
    """The URL is absent, malformed, or names an engine this project does not support."""


def _split(url: str) -> tuple[str, str, str, str, int, str, dict[str, str]]:
    if not url:
        raise DatabaseUrlError("DATABASE_URL is empty")

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in POSTGRES_SCHEMES:
        raise DatabaseUrlError(
            f"unsupported database scheme {scheme!r}. This project is Postgres 17 only "
            f"(decision D2): the whole provenance model assumes native uuid, numeric, "
            f"and JSONB. Expected one of {POSTGRES_SCHEMES}."
        )

    name = unquote(parsed.path.lstrip("/"))
    if not name:
        raise DatabaseUrlError(f"DATABASE_URL has no database name: {url!r}")

    return (
        scheme,
        unquote(parsed.username or "") or "postgres",
        unquote(parsed.password or ""),
        parsed.hostname or "localhost",
        parsed.port or 5432,
        name,
        dict(parse_qsl(parsed.query)),
    )


def parse(
    url: str,
    *,
    conn_max_age: int = 0,
    connect_timeout: int = 10,
    statement_timeout_ms: int = 30_000,
) -> dict:
    """
    Return a Django ``DATABASES['default']`` dict.

    ``conn_max_age`` defaults to 0 because the deployed topology puts PgBouncer in
    transaction mode in front of RDS (§3.4). Persistent Django connections behind a
    transaction pooler hold server-side sessions open and reintroduce bottleneck
    B10 — connection count coupled to process count, and deploys as a failure
    window while old and new processes overlap.
    """
    _, user, password, host, port, name, query = _split(url)

    options: dict[str, object] = {"connect_timeout": connect_timeout}
    if statement_timeout_ms > 0:
        # A runaway provenance join must fail rather than pin a burstable
        # instance's CPU credits (§3.4 burstable caveat).
        options["options"] = f"-c statement_timeout={statement_timeout_ms}"
    if "sslmode" in query:
        options["sslmode"] = query["sslmode"]

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": str(port),
        "CONN_MAX_AGE": conn_max_age,
        "OPTIONS": options,
    }


def to_psycopg_dsn(url: str) -> str:
    """
    Return a libpq DSN for ``psycopg.connect``.

    Used by the bulk ``COPY`` path in normalisation (bottleneck B3), which bypasses
    the ORM entirely and therefore needs its own connection string.
    """
    _, user, password, host, port, name, query = _split(url)
    parts = [f"host={host}", f"port={port}", f"dbname={name}", f"user={user}"]
    if password:
        parts.append(f"password={password}")
    if "sslmode" in query:
        parts.append(f"sslmode={query['sslmode']}")
    return " ".join(parts)


def to_sqlalchemy_url(url: str) -> str:
    """
    Return a SQLAlchemy URL pinned to the psycopg 3 driver.

    The pipeline uses SQLAlchemy **Core** against Django-migrated tables — no
    second migration tool, no ``Base.metadata.create_all`` (§3.2 rule 1).
    """
    _, user, password, host, port, name, query = _split(url)
    auth = user if not password else f"{user}:{password}"
    base = f"postgresql+psycopg://{auth}@{host}:{port}/{name}"
    if "sslmode" in query:
        base += f"?sslmode={query['sslmode']}"
    return base


__all__ = ["DatabaseUrlError", "POSTGRES_SCHEMES", "parse", "to_psycopg_dsn", "to_sqlalchemy_url"]
