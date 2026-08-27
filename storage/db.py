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

import psycopg2


def connection_url() -> str | None:
    """The connection URL to use, or None when falling back to POSTGRES_*."""
    return os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")


def connect(autocommit: bool = False):
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
        conn = psycopg2.connect(url)
    else:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5433"),
            dbname=os.getenv("POSTGRES_DB", "signal"),
            user=os.getenv("POSTGRES_USER", "signal"),
            password=os.getenv("POSTGRES_PASSWORD", "signal"),
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
