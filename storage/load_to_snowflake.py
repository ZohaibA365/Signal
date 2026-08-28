"""
Copy warehouse source tables from Postgres into Snowflake.

Snowflake is an additional target, not a replacement: Neon stays the live
warehouse behind the dashboard, and this exists so the same dbt models build
in both. Only SOURCE tables are copied - every model is rebuilt by dbt in
Snowflake rather than shipped across, which is what actually proves the
project is not welded to one engine.

Two type conversions matter. Postgres arrays (TEXT[]) and JSONB have no direct
pandas equivalent that write_pandas will accept, so they are serialised to
JSON strings. The dbt models only ever select these columns, so nothing
downstream changes.

Usage:
    python storage/load_to_snowflake.py
    python storage/load_to_snowflake.py --tables raw_postings
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings

import pandas as pd
import snowflake.connector as sf
from dotenv import load_dotenv
from snowflake.connector.pandas_tools import write_pandas

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("load_to_snowflake")

# The source tables every dbt model reads from. Models are rebuilt by dbt.
TABLES = [
    "raw_postings",
    "posting_technologies",
    "job_enrichment",
    "market_snapshots",
    "market_snapshot_companies",
    "market_snapshot_salary",
    "dol_employer_summary",
    "company_employer_key",
]


def snowflake_conn():
    return sf.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        database=os.getenv("SNOWFLAKE_DATABASE", "SIGNAL_DB"),
        schema=os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        login_timeout=30,
    )


def serialise_complex(df: pd.DataFrame) -> pd.DataFrame:
    """
    Postgres arrays and JSONB have no pandas dtype write_pandas accepts, so
    they become JSON strings. Every dbt model only selects these columns, so
    the change is invisible downstream.
    """
    for col in df.columns:
        sample = df[col].dropna().head(1)
        if len(sample) and isinstance(sample.iloc[0], (list, dict)):
            df[col] = df[col].map(lambda v: json.dumps(v) if v is not None else None)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy source tables to Snowflake")
    ap.add_argument("--tables", nargs="+", default=TABLES)
    args = ap.parse_args()

    pg = connect(autocommit=True)
    sc = snowflake_conn()
    log.info("Loading %s table(s) into Snowflake", len(args.tables))

    for table in args.tables:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            df = pd.read_sql(f"SELECT * FROM {table}", pg)
        if df.empty:
            log.warning("  %-28s empty, skipped", table)
            continue

        df = serialise_complex(df)
        # write_pandas matches on exact case; Snowflake upper-cases unquoted
        # identifiers, so the frame is upper-cased to match what it creates.
        df.columns = [c.upper() for c in df.columns]

        ok, nchunks, nrows, _ = write_pandas(
            sc, df, table.upper(), auto_create_table=True, overwrite=True,
            quote_identifiers=False,
            # Required for timezone-aware timestamps. Without it the connector
            # warns that datetimes "can be incorrectly written" - it writes the
            # wall-clock value and drops the offset, which silently shifts
            # every posted_date and enriched_at by the UTC offset. Freshness
            # checks and trend models read those columns, so a quiet shift
            # would corrupt the index rather than fail.
            use_logical_type=True,
        )
        log.info("  %-28s %s rows in %s chunk(s)  %s",
                 table, f"{nrows:,}", nchunks, "OK" if ok else "FAILED")

    pg.close()
    sc.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
