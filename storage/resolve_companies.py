"""
Collapse spellings of one employer into a single canonical company name.

The site published 34 pages that were duplicates of another page: `databricks`
and `databricks-inc`, `oracle` and `oracle-corporation`, `stripe` and
`stripe-inc`, `snowflake` and `snowflake-inc`, 33 groups in all. Each variant
got its own company page, its own peer computation, its own row in the
sponsorship mapping, and therefore potentially a sponsorship section on one
page and "No Department of Labor filings matched this employer" on its twin.

Worse, three slugs collided outright. site/build.py renders companies in
descending posting order and writes `companies/<slug>/index.html`, so
`/companies/fivetran/` was written for "FiveTran" with 177 postings and then
overwritten by "Fivetran" with 29. The published page understated that employer
six-fold and sitemap.xml listed the URL twice. Nothing detected it.

No new matching logic is involved. `normalise_employer` - the function that
already canonicalises both sides of the DOL join - catches every one of the 33
groups, so this is a GROUP BY rather than an algorithm. Reusing it also keeps
one normaliser in one language: a second copy expressed in SQL would drift, and
the dbt models are meant to stay portable across engines whose regex and string
functions differ.

Why a mapping table rather than changing the grain. `company_name` is the join
key in six models plus two site queries and the alerter. Introducing a
surrogate key would ripple through all of them. Instead stg_jobs.sql left-joins
this table and projects the canonical name, so every downstream model keeps
joining on `company_name` and keeps working - the value changes for 34
companies, the definition does not.

Canonical choice is the variant with the most postings, tie-broken by the
shortest name. That preserves the URL of the larger page, which is the one more
likely to be indexed.

Usage:
    python storage/resolve_companies.py
    python storage/resolve_companies.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestion"))
from db import connect, describe  # noqa: E402
from dol_ingest import normalise_employer  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("resolve_companies")

DDL = """
CREATE TABLE IF NOT EXISTS company_identity (
    company_name   TEXT PRIMARY KEY,
    company_key    TEXT NOT NULL,
    canonical_name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_identity_key ON company_identity (company_key);
"""


def resolve(rows: list[tuple[str, int]]) -> list[tuple[str, str, str]]:
    """
    Map every spelling to its canonical one.

    `rows` is (company_name, postings). Pure function over that list so it can
    be tested without a database.
    """
    groups: dict[str, list[tuple[str, int]]] = {}
    for name, postings in rows:
        key = normalise_employer(name)
        if not key:
            continue
        groups.setdefault(key, []).append((name, postings or 0))

    out = []
    for key, members in groups.items():
        # Most postings wins; shortest name breaks a tie; the name itself breaks
        # that, so the result is deterministic rather than dependent on row order.
        canonical = sorted(members, key=lambda m: (-m[1], len(m[0]), m[0]))[0][0]
        out += [(name, key, canonical) for name, _ in members]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Canonicalise company names")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    conn = connect()
    log.info("Resolving against %s", describe())
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""
                SELECT nullif(trim(company_name), ''), count(*)
                FROM raw_postings
                WHERE company_name IS NOT NULL
                GROUP BY 1
            """)
            rows = [(n, c) for n, c in cur.fetchall() if n]
            mapping = resolve(rows)

            merged = {}
            for name, _key, canonical in mapping:
                if name != canonical:
                    merged.setdefault(canonical, []).append(name)
            log.info("%s names -> %s identities; %s groups collapse %s duplicate names",
                     f"{len(rows):,}", f"{len({m[1] for m in mapping}):,}",
                     len(merged), sum(len(v) for v in merged.values()))
            for canonical, others in sorted(merged.items(),
                                            key=lambda kv: -len(kv[1]))[:15]:
                log.info("    %-34s <- %s", canonical, ", ".join(others))

            if args.dry_run:
                log.info("dry run - nothing written")
                return

            cur.execute("TRUNCATE company_identity")
            execute_values(cur,
                           "INSERT INTO company_identity "
                           "(company_name, company_key, canonical_name) VALUES %s",
                           mapping, page_size=1000)
            log.info("Wrote %s identity rows", f"{len(mapping):,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
