"""
Apply storage/snowflake_setup.sql to the account.

Idempotent: every statement is CREATE IF NOT EXISTS, ALTER, or GRANT, so running
it twice changes nothing. That is what lets it be committed and re-run rather
than remembered.

Order matters and is the point. The resource monitor is created and attached
before anything else, so there is no window in which a warehouse can run
uncapped. This project has already been surprised by one bill.

Usage:
    python storage/snowflake_apply.py
    python storage/snowflake_apply.py --show      # report state, change nothing
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from snowflake_db import connect, describe  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("snowflake_apply")

SETUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snowflake_setup.sql")


def statements(sql: str) -> list[str]:
    """
    Split on semicolons, dropping comments and blanks.

    Naive by design: the file is DDL with no string literals containing
    semicolons, and a real parser here would be more code than the thing it
    parses. If that ever stops being true, this breaks loudly rather than
    silently running half a statement.
    """
    stripped = "\n".join(line for line in sql.split("\n")
                         if not line.strip().startswith("--"))
    return [s.strip() for s in stripped.split(";") if s.strip()]


def report(cur) -> None:
    """The state that matters: is spending capped, and as whom do we connect."""
    for label, query in [
        ("resource monitors", "SHOW RESOURCE MONITORS"),
        ("warehouses", "SHOW WAREHOUSES"),
    ]:
        try:
            cur.execute(query)
            rows = cur.fetchall()
            cols = [c[0].lower() for c in cur.description]
            log.info("  %s: %s", label, len(rows))
            for r in rows:
                d = dict(zip(cols, r, strict=False))
                if label == "resource monitors":
                    log.info("    %-16s quota=%s used=%s%% frequency=%s",
                             d.get("name"), d.get("credit_quota"),
                             d.get("used_credits"), d.get("frequency"))
                else:
                    log.info("    %-16s size=%-8s auto_suspend=%-5s monitor=%s state=%s",
                             d.get("name"), d.get("size"), d.get("auto_suspend"),
                             d.get("resource_monitor"), d.get("state"))
        except Exception as exc:                     # noqa: BLE001
            log.warning("  %s: %s", label, str(exc).strip()[:140])


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply Snowflake account setup")
    ap.add_argument("--show", action="store_true", help="report state without changing it")
    args = ap.parse_args()

    log.info("Target: %s", describe())
    conn = connect()
    try:
        with conn.cursor() as cur:
            if args.show:
                report(cur)
                return
            for i, stmt in enumerate(statements(open(SETUP).read()), 1):
                head = re.sub(r"\s+", " ", stmt)[:78]
                try:
                    cur.execute(stmt)
                    log.info("  %2d. ok   %s", i, head)
                except Exception as exc:             # noqa: BLE001
                    log.error("  %2d. FAIL %s", i, head)
                    log.error("      %s", str(exc).strip()[:220])
                    raise SystemExit(1) from exc
            log.info("Applied %s statement(s).", i)
            report(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
