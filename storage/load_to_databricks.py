"""
Copy the warehouse's source tables into Databricks, so dbt can build there.

This exists for one reason: Databricks fails differently from Snowflake, and a
portability claim is only worth making if something tests it on an engine that
breaks in a new way.

Snowflake is the regex oracle. Its regexp_like is implicitly anchored where
Postgres searches anywhere, which made identical SQL classify 1,590 postings as
internships on one engine and zero on the other, compiling cleanly on both. Spark
SQL's rlike is unanchored like Postgres, so Databricks genuinely cannot catch that
class.

Databricks is the type-coercion oracle. Subtracting two dates gives an INTERVAL on
Spark rather than an integer, so days_since_posted changes type and a comparison
against 60 misbehaves - the same shape of bug as the regex one: it compiles, and
the answer is wrong. It also exercises arrays, timestamps with time zones,
date_trunc's argument order and ANSI coercion rules, none of which Postgres and
Snowflake disagree about.

How the copy works, and why not some other way. Free Edition gives one 2X-Small
serverless SQL warehouse and no ability to run arbitrary JDBC loads, so the route
is the one the DOL job already uses: write Parquet locally, PUT it into a Unity
Catalog Volume over the Files API, and have the warehouse read it in place. That
is three moving parts rather than one, and it is the only one of the three that
Free Edition actually permits.

Nothing in the serving path depends on any of this. The site reads Postgres.

Usage:
    python storage/load_to_databricks.py
    python storage/load_to_databricks.py --tables raw_postings
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import warnings

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect as pg_connect  # noqa: E402
from mirror_tables import TABLES, serialise_complex  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("load_to_databricks")

# The Volume the DOL job already uses. Re-used rather than a second one created:
# Free Edition's catalog is small enough that one staging area is easier to reason
# about than two.
VOLUME = "/Volumes/workspace/signal_dol/lake/mirror"
CATALOG = os.getenv("DATABRICKS_CATALOG", "workspace")
SCHEMA = os.getenv("DATABRICKS_SCHEMA", "signal")


class Databricks:
    """The two APIs this needs: Files for the upload, SQL for the tables."""

    def __init__(self) -> None:
        self.host = (os.getenv("DATABRICKS_HOST") or "").rstrip("/")
        self.token = os.getenv("DATABRICKS_TOKEN") or ""
        self.warehouse = os.getenv("DATABRICKS_WAREHOUSE_ID") or ""
        if not self.host or not self.token:
            raise SystemExit("DATABRICKS_HOST and DATABRICKS_TOKEN must be set")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def upload(self, local: str, remote: str) -> None:
        """PUT one file into a Volume, overwriting what is there."""
        with open(local, "rb") as fh:
            r = self.session.put(
                f"{self.host}/api/2.0/fs/files{remote}",
                params={"overwrite": "true"}, data=fh, timeout=600,
            )
        if r.status_code >= 300:
            raise SystemExit(f"upload of {remote} failed: {r.status_code} {r.text[:300]}")

    def warehouse_id(self) -> str:
        """The SQL warehouse to run statements on, discovered if not configured."""
        if self.warehouse:
            return self.warehouse
        r = self.session.get(f"{self.host}/api/2.0/sql/warehouses", timeout=60)
        r.raise_for_status()
        houses = r.json().get("warehouses", [])
        if not houses:
            raise SystemExit("this workspace has no SQL warehouse to build on")
        # Free Edition has exactly one. Naming it in .env is still supported, for a
        # workspace that has several and a preference about which.
        self.warehouse = houses[0]["id"]
        log.info("using SQL warehouse %s (%s)", houses[0]["name"], self.warehouse)
        return self.warehouse

    def sql(self, statement: str) -> list:
        """
        Run one statement and wait for it.

        wait_timeout is the API's own cap at 50s; anything longer comes back as a
        pending statement id to poll, which is what the loop handles. A stopped
        warehouse takes a couple of minutes to wake, and that wake is the single
        slowest thing here.
        """
        body = {
            "warehouse_id": self.warehouse_id(),
            "statement": statement,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
            "catalog": CATALOG,
            "schema": SCHEMA,
        }
        r = self.session.post(f"{self.host}/api/2.0/sql/statements", json=body, timeout=120)
        r.raise_for_status()
        out = r.json()

        while out["status"]["state"] in ("PENDING", "RUNNING"):
            sid = out["statement_id"]
            r = self.session.get(f"{self.host}/api/2.0/sql/statements/{sid}", timeout=120)
            r.raise_for_status()
            out = r.json()

        state = out["status"]["state"]
        if state != "SUCCEEDED":
            message = out["status"].get("error", {}).get("message", state)
            raise SystemExit(f"statement failed: {message}\n  {statement[:300]}")
        return out.get("result", {}).get("data_array") or []


def mirror(db: Databricks, pg, table: str) -> int:
    """One table: Postgres to Parquet to Volume to Delta."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        df = pd.read_sql(f"SELECT * FROM {table}", pg)
    if df.empty:
        log.warning("  %-28s empty, skipped", table)
        return 0

    df = serialise_complex(df)
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{table}.parquet")
        # Microsecond timestamps, not pandas' default nanoseconds. Spark reads
        # Parquet timestamps at microsecond precision and refuses the rest
        # outright: "Illegal Parquet type: INT64 (TIMESTAMP(NANOS,true))". Nothing
        # here is measured finer than a second, so the truncation is free.
        df.to_parquet(local, index=False, coerce_timestamps="us",
                      allow_truncated_timestamps=True)
        remote = f"{VOLUME}/{table}.parquet"
        db.upload(local, remote)

    # CREATE OR REPLACE, not INSERT: this is a mirror, and a mirror that appends is
    # a mirror that double-counts. Reading the Parquet in place means the warehouse
    # does the typing, which is the point - its choices are what parity tests.
    db.sql(f"""
        CREATE OR REPLACE TABLE {CATALOG}.{SCHEMA}.{table}
        AS SELECT * FROM read_files('{remote}', format => 'parquet')
    """)
    log.info("  %-28s %s rows  OK", table, f"{len(df):,}")
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy source tables to Databricks")
    ap.add_argument("--tables", nargs="+", default=TABLES)
    args = ap.parse_args()

    db = Databricks()
    db.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    log.info("Loading %s table(s) into %s.%s", len(args.tables), CATALOG, SCHEMA)

    pg = pg_connect(autocommit=True)
    try:
        total = sum(mirror(db, pg, table) for table in args.tables)
    finally:
        pg.close()
    log.info("Done. %s rows mirrored.", f"{total:,}")


if __name__ == "__main__":
    main()
