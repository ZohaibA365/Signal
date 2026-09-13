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
import logging
import os
import sys
import warnings

import pandas as pd
from dotenv import load_dotenv
from snowflake.connector.pandas_tools import write_pandas

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect as pg_connect  # noqa: E402
from mirror_tables import TABLES, serialise_complex  # noqa: E402
from snowflake_db import connect, describe  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("load_to_snowflake")



def snowflake_conn():
    """
    Delegates to storage/snowflake_db.py, which uses key-pair auth.

    This used to pass a password and default the role to ACCOUNTADMIN. Both were
    wrong. The account now enforces MFA, which a password-presenting driver
    cannot satisfy at all - and ACCOUNTADMIN for a loader that writes eight
    tables is the kind of grant that matters precisely because a CI credential is
    the one most likely to leak.
    """
    return connect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy source tables to Snowflake")
    ap.add_argument("--tables", nargs="+", default=TABLES)
    args = ap.parse_args()

    pg = pg_connect(autocommit=True)
    sc = snowflake_conn()
    log.info("Loading %s table(s) into %s", len(args.tables), describe())

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
