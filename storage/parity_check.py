"""
Compare model output between Postgres and Snowflake.

A build that succeeds on both engines is not a portability test. The bug this
project most fears compiled cleanly on both and returned different answers: an
unanchored regex matched 1,590 postings as internships on Postgres and zero on
Snowflake, because Postgres regexp_like searches anywhere while Snowflake's is
implicitly anchored. Nothing failed. The number was simply wrong on one side.

So the artefact worth having is not "it builds" but "they agree", and that means
running the same questions against both and comparing the answers.

Every check is a single scalar, deliberately. A scalar is unambiguous to compare,
cheap on a 2X-Small warehouse, and a difference points at one model rather than a
diff of 50,000 rows. The set is chosen to cover the predicates that have actually
been wrong before: the internship classifier, the sponsorship refusal, the
company grain, and the joins that carry them into the marts.

Usage:
    python storage/parity_check.py
    python storage/parity_check.py --json        # for a workflow summary
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect as pg_connect  # noqa: E402
from snowflake_db import connect as sf_connect  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("parity")

# Each entry is one scalar that must match. Named for what a difference would
# mean, not for the SQL.
CHECKS: dict[str, str] = {
    # The exact bug class this exists for. A regex whose anchoring differs shows
    # up here and nowhere else.
    "postings classified as internships":
        "select count(*) from stg_jobs where is_internship",
    "postings with seniority intern":
        "select count(*) from stg_jobs where seniority = 'intern'",
    "postings whose text refuses sponsorship":
        "select count(*) from stg_jobs where refuses_sponsorship",
    "postings whose text offers sponsorship":
        "select count(*) from stg_jobs where offers_sponsorship",
    "postings seniority entry":
        "select count(*) from stg_jobs where seniority = 'entry'",
    "postings seniority senior":
        "select count(*) from stg_jobs where seniority = 'senior'",
    "stale postings":
        "select count(*) from stg_jobs where is_stale",

    # Grain. A company-name change on one engine would show here first.
    "rows in stg_jobs": "select count(*) from stg_jobs",
    "companies in dim_company": "select count(*) from dim_company",
    "distinct companies in stg_jobs":
        "select count(distinct company_name) from stg_jobs",

    # The sponsorship join, which is the feature most often wrong.
    "companies with confident sponsorship evidence":
        "select count(*) from int_company_sponsorship where is_confident_match",
    "total filings attributed to a company":
        "select sum(total_filings) from int_company_sponsorship where is_confident_match",

    # The marts the site reads.
    "rows in apply_queue": "select count(*) from apply_queue",
    "publishable rows in apply_queue":
        "select count(*) from apply_queue where link_tier = 'direct'",
    "intern/entry publishable rows":
        "select count(*) from apply_queue where link_tier = 'direct' "
        "and seniority in ('intern','entry')",
    "rows in ranked_opportunities": "select count(*) from ranked_opportunities",
    "technologies tracked": "select count(*) from dim_technology",
}


def scalar(cur, sql: str):
    cur.execute(sql)
    row = cur.fetchone()
    value = row[0] if row else None
    # Snowflake returns NUMBER as Decimal where Postgres returns int. Compare the
    # numbers, not their Python types, or every aggregate looks like a mismatch.
    return int(value) if value is not None else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare model output across engines")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    pg = pg_connect(autocommit=True)
    sf = sf_connect()
    results = []
    try:
        with pg.cursor() as pc, sf.cursor() as sc:
            for name, sql in CHECKS.items():
                try:
                    a = scalar(pc, sql)
                except Exception as exc:                      # noqa: BLE001
                    a, pg_err = None, str(exc).strip()[:80]
                    pg.rollback()
                    log.error("  postgres failed on %r: %s", name, pg_err)
                try:
                    b = scalar(sc, sql)
                except Exception as exc:                      # noqa: BLE001
                    b = None
                    log.error("  snowflake failed on %r: %s", name, str(exc).strip()[:80])
                results.append({"check": name, "postgres": a, "snowflake": b,
                                "match": a == b})
    finally:
        pg.close()
        sf.close()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        width = max(len(r["check"]) for r in results)
        log.info("%-*s %14s %14s", width, "check", "postgres", "snowflake")
        for r in results:
            log.info("%-*s %14s %14s  %s", width, r["check"],
                     f"{r['postgres']:,}" if r["postgres"] is not None else "-",
                     f"{r['snowflake']:,}" if r["snowflake"] is not None else "-",
                     "ok" if r["match"] else "DIFFERS")

    bad = [r for r in results if not r["match"]]
    if bad:
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::{len(bad)} parity check(s) differ between Postgres and "
                  f"Snowflake: {', '.join(r['check'] for r in bad)}")
        raise SystemExit(f"{len(bad)} of {len(results)} checks differ")
    log.info("All %s checks agree.", len(results))


if __name__ == "__main__":
    main()
