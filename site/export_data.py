"""
Hand the warehouse's answers to the React build as JSON, and nothing more.

The site is being rebuilt in React, and the obvious temptation is to let Node talk
to Postgres. That would mean reimplementing site/queries.py - seventeen statements
that already know the 20,000-row cap, the sponsorship join, and which of them are
allowed to come back empty - in a second language, where they could then disagree
with the first. The queries are the part that is hard to get right and they are
already right.

So Python keeps the warehouse and gains one job: read, and write JSON. The Next
build reads those files and knows nothing about a database. Nothing here computes
or decides anything; if a figure needs deriving, it is derived where it always was.

    python site/export_data.py                  # writes web/data/*.json
    python site/export_data.py --out some/dir
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "storage"))
sys.path.insert(0, str(HERE))

import queries as Q  # noqa: E402
from db import connect, describe  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# An explicit path, not discovery. find_dotenv() walks up from the CWD and raises
# outright when the process has no real working directory, which has bitten this
# project more than once. CI sets the variable itself and this is a no-op there.
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("export_data")

DEFAULT_OUT = ROOT / "web" / "data"

# Which query feeds which file. Named rather than dumped wholesale so the React
# side imports a file whose contents are obvious from its name, and so a query
# added to queries.py does not silently start shipping to the public site.
FILES: dict[str, tuple[str, ...]] = {
    "stats": ("CORPUS_STATS", "FRESHNESS"),
    "companies": ("COMPANIES",),
    "company_detail": ("COMPANY_TECH", "COMPANY_ROLES", "COMPANY_PEERS",
                       "COMPANY_MARKET_POSITION"),
    "tech": ("TECH_DETAIL", "TECH_EMPLOYERS"),
    "market": ("DEMAND_BY_CATEGORY", "SALARY_LEADERS", "STACK_PAIRS"),
}

# The history layer, which is allowed to be empty and must not fail the export.
OPTIONAL_FILES: dict[str, tuple[str, ...]] = {
    "history": ("HISTORY_COVERAGE", "COMPANY_PACE", "TECH_HISTORY"),
    "retired": ("RETIRED_COMPANIES",),
}


def rows(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def encode(value):
    """
    JSON cannot carry what Postgres hands back.

    Decimal becomes float rather than str: every consumer of these numbers formats
    or compares them, and a quoted "12.5" silently sorts as text. The precision lost
    is irrelevant at the magnitudes here - percentages and counts - and the
    alternative is every call site remembering to parse.
    """
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable")


def write(out: Path, name: str, payload: dict) -> None:
    path = out / f"{name}.json"
    text = json.dumps(payload, default=encode, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    log.info("  %-16s %8s rows  %7.1f kB", name,
             sum(len(v) for v in payload.values() if isinstance(v, list)),
             len(text.encode()) / 1024)


def main() -> None:
    ap = argparse.ArgumentParser(description="Export warehouse data for the React build")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="directory to write into")
    args = ap.parse_args()

    # The same refusal site/build.py makes, for the same reason: without a
    # connection string in CI the failure would surface as a missing relation
    # rather than as the absent credential it actually is.
    if os.getenv("CI") and not (os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")):
        raise SystemExit(
            "DATABASE_URL is not set. The site is generated from the warehouse, so "
            "the export needs a connection string."
        )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()

    conn = connect(autocommit=True)
    cur = conn.cursor()
    log.info("Querying %s", describe())
    try:
        for name, wanted in FILES.items():
            payload: dict = {}
            for key in wanted:
                payload[key] = rows(cur, getattr(Q, key))
                if not payload[key]:
                    # Same contract as build.py's fetch_all: these are required, and
                    # an empty one means the warehouse is mid-rebuild. Publishing a
                    # site with no companies on it is worse than not publishing.
                    raise SystemExit(f"{key} returned no rows - refusing to export")
            write(out, name, payload)

        for name, wanted in OPTIONAL_FILES.items():
            payload = {}
            for key in wanted:
                try:
                    payload[key] = rows(cur, Q.may_be_empty[key])
                except Exception as exc:  # noqa: BLE001
                    # A missing history table is not a reason to have no site. The
                    # pages that use these already treat absence as "say nothing".
                    log.warning("  %s unavailable (%s); exporting empty",
                                key, type(exc).__name__)
                    conn.rollback()
                    payload[key] = []
            write(out, name, payload)
    finally:
        conn.close()

    meta = {"generated_at": datetime.now(UTC).isoformat(),
            "site_url": os.getenv("SITE_URL", "https://zohaiba365.github.io/Signal"),
            "repo_url": "https://github.com/ZohaibA365/Signal"}
    write(out, "meta", meta)

    log.info("Exported %s file(s) to %s in %.1fs",
             len(FILES) + len(OPTIONAL_FILES) + 1, out, time.time() - started)


if __name__ == "__main__":
    main()
