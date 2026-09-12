"""
Apply storage/schema.sql to the warehouse.

schema.sql calls itself the single source of truth for structure, and CI applies
it on every run - but nothing applied it to the warehouse behind the site. New
columns reached production only when someone remembered to run an ALTER by hand,
and that is exactly how the pipeline broke: raw_postings gained
description_dropped_at in schema.sql and in CI, the loader's UPSERT began
referencing it, and both loads then failed against a production table that had
never heard of the column.

A declaration nothing applies is not a source of truth. This makes the file
authoritative in the place that matters, and it is safe to run before every load
because schema.sql is entirely additive: 18 CREATE TABLE IF NOT EXISTS, 14
CREATE INDEX IF NOT EXISTS, 4 ADD COLUMN IF NOT EXISTS, and nothing that drops,
deletes or rewrites. A migration that removes something would have to be
deliberate and separate, which is the right amount of friction for it.

Python rather than psql because the runners do not install a Postgres client and
the one workflow that does install it only does so for CI.

Usage:
    python storage/apply_schema.py
    python storage/apply_schema.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect, describe  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("apply_schema")

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

# Anything that could lose data has no business running unattended before every
# load. This is a guard against the file changing, not against what it says today.
FORBIDDEN = re.compile(
    r"^\s*(DROP\s+(TABLE|COLUMN|INDEX|VIEW|SCHEMA|DATABASE)|TRUNCATE|DELETE\s+FROM"
    r"|ALTER\s+TABLE\s+\S+\s+(DROP|RENAME|ALTER))",
    re.IGNORECASE | re.MULTILINE,
)


def check_is_additive(sql: str) -> None:
    """Refuse to run a schema file that could destroy anything."""
    found = FORBIDDEN.findall(sql)
    if found:
        raise SystemExit(
            f"storage/schema.sql contains {len(found)} statement(s) that could lose "
            f"data, so it will not be applied automatically. Run a deliberate "
            f"migration instead."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply storage/schema.sql to the warehouse")
    ap.add_argument("--dry-run", action="store_true",
                    help="check the file is additive, then stop")
    args = ap.parse_args()

    with open(SCHEMA) as fh:
        sql = fh.read()
    check_is_additive(sql)

    # Counted for the log only. The file is NOT split on semicolons to run:
    # schema.sql is mostly explanation, and its comments contain semicolons, so
    # splitting cuts a sentence in half and then tries to execute the second half.
    bare = re.sub(r"--[^\n]*", "", sql)
    count = len([s for s in bare.split(";") if s.strip()])
    log.info("schema.sql is additive: %s statement(s)", count)
    if args.dry_run:
        return

    log.info("applying to %s", describe())
    conn = connect()
    try:
        with conn.cursor() as cur:
            # The whole file in one call. psycopg2 accepts multiple statements, and
            # Postgres reports the line and position of whichever one fails, which
            # is the attribution that matters. Every statement is guarded by IF NOT
            # EXISTS, so re-running costs nothing and a partial apply cannot leave
            # the schema half-built - it is one transaction.
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()
    log.info("schema applied")


if __name__ == "__main__":
    main()
