"""
One place that decides how to reach the warehouse.

Signal runs against two databases: local Postgres in Docker for development,
and hosted Neon for the public dashboard. Streamlit Community Cloud injects
configuration as a single connection URL rather than as separate host/port/
user variables, so the resolution order is:

    1. DATABASE_URL          - what Streamlit Cloud and most PaaS provide
    2. NEON_DATABASE_URL     - the hosted warehouse, when explicitly targeted
    3. POSTGRES_* variables  - local development default

Without this, every script grew its own copy of the connection logic and
deploying meant editing each one.
"""

from __future__ import annotations

import os
import re

import psycopg2


def connection_url() -> str | None:
    """The connection URL to use, or None when falling back to POSTGRES_*."""
    return os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")


def _normalise(url: str) -> str:
    """
    Make a Neon URL portable across platforms.

    channel_binding=require demands SCRAM channel binding, which depends on
    the client's OpenSSL build. It succeeds with macOS's OpenSSL and is the
    known-fragile parameter on Linux CI images. Dropping it costs nothing:
    sslmode=require still encrypts the connection, and it is the parameter
    most likely to explain a connection that works locally and fails on a
    runner.
    """
    return re.sub(r"[?&]channel_binding=[^&]*", "", url)


def connect(autocommit: bool = False, cursor_factory=None):
    """
    Open a warehouse connection.

    Pass autocommit=True for anything read-only and long-lived - notably the
    dashboard. psycopg2 opens a transaction on the first SELECT and a cached
    connection never commits it, which leaves the session "idle in
    transaction", pinning vacuum and blocking DDL. A dashboard left open did
    exactly that for 22 hours.
    """
    url = connection_url()
    if url:
        # Neon suspends idle compute, so the first connection of the day waits
        # for it to wake - measured at minutes, not seconds. A short default
        # timeout turns that wake into a spurious failure.
        timeout = int(os.getenv("PG_CONNECT_TIMEOUT", "60"))
        primary = _normalise(url)
        kw = {"connect_timeout": timeout}
        if cursor_factory is not None:
            kw["cursor_factory"] = cursor_factory
        try:
            conn = psycopg2.connect(primary, **kw)
        except psycopg2.OperationalError:
            # Fall back from the pooled endpoint to the direct one. PgBouncer
            # is right for many short connections and is the more fragile of
            # the two for a single long-lived batch job.
            direct = primary.replace("-pooler", "")
            if direct == primary:
                raise
            conn = psycopg2.connect(direct, **kw)
    else:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5433"),
            dbname=os.getenv("POSTGRES_DB", "signal"),
            user=os.getenv("POSTGRES_USER", "signal"),
            password=os.getenv("POSTGRES_PASSWORD", "signal"),
            **({"cursor_factory": cursor_factory} if cursor_factory else {}),
        )
    conn.autocommit = autocommit
    return conn


def describe() -> str:
    """Human-readable target, safe to log - never includes the password."""
    url = connection_url()
    if url:
        host = url.split("@")[-1].split("/")[0] if "@" in url else "hosted"
        return f"hosted warehouse ({host})"
    return f"local ({os.getenv('POSTGRES_HOST','localhost')}:{os.getenv('POSTGRES_PORT','5433')})"
