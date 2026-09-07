"""
Materialise technology mentions per posting.

dbt cannot call the Python matcher in ai_layer/taxonomy.py, so matches are
written to `posting_technologies` and joined from SQL. This is the bridge
between the controlled vocabulary and the warehouse models.

Cheap and deterministic: pure regex, no API calls, no cost. Safe to re-run
whenever the taxonomy changes - which is the point, since re-running the
whole history after adding a technology is how the index stays consistent.

Usage:
    python ai_layer/extract_tech.py            # only postings not yet matched
    python ai_layer/extract_tech.py --rebuild  # re-match everything
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(__file__))
from taxonomy import BY_SLUG, match_technologies  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
from db import connect  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("extract_tech")

BATCH = 5000


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract technology mentions from postings")
    ap.add_argument("--rebuild", action="store_true",
                    help="clear existing matches and re-match every posting")
    args = ap.parse_args()

    # The shared connection, not the POSTGRES_* variables. Reading those
    # directly targets the local container while dbt and the site read the
    # hosted warehouse - and in CI they are not set at all, so this step
    # dialled a localhost that does not exist there and failed every run.
    # Read on one connection and close it before the matching starts.
    #
    # The matching loop runs for minutes over tens of thousands of postings,
    # and Neon closes a connection left idle underneath it: a run over 31,000
    # new postings did all the work and then lost it to "connection already
    # closed" at the write. The same failure had already appeared in the
    # discovery sweep and in scoring, for the same reason - a long job holding
    # one connection across it.
    conn = connect()
    try:
        with conn.cursor() as cur:
            if args.rebuild:
                cur.execute("TRUNCATE posting_technologies")
                conn.commit()
                log.info("Cleared existing matches (--rebuild)")

            # Match the title as well as the description: the title often
            # names the technology ("Snowflake Data Engineer") when the
            # truncated description does not.
            cur.execute("""
                SELECT r.source, r.job_id,
                       coalesce(r.job_title,'') || ' ' || coalesce(r.description_raw,'')
                FROM raw_postings r
                WHERE %s OR NOT EXISTS (
                    SELECT 1 FROM posting_technologies p
                    WHERE p.source = r.source AND p.job_id = r.job_id
                )
            """, (args.rebuild,))
            rows = cur.fetchall()
    finally:
        conn.close()

    log.info("%s postings to match against %s technologies",
             f"{len(rows):,}", len(BY_SLUG))

    pairs: list[tuple] = []
    matched_postings = 0
    for source, job_id, text in rows:
        slugs = match_technologies(text)
        if slugs:
            matched_postings += 1
            pairs.extend((source, job_id, s) for s in slugs)

    # Each batch on its own connection, so the write outlives any single one
    # the warehouse is willing to hold open.
    written = 0
    for i in range(0, len(pairs), BATCH):
        conn = connect()
        try:
            with conn, conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO posting_technologies (source, job_id, tech_slug)
                    VALUES %s ON CONFLICT DO NOTHING
                """, pairs[i:i + BATCH], page_size=1000)
            written += len(pairs[i:i + BATCH])
        finally:
            conn.close()

    pct = (matched_postings / len(rows) * 100) if rows else 0
    log.info("%s postings matched at least one technology (%.0f%%), %s mentions written",
             f"{matched_postings:,}", pct, f"{written:,}")

    conn.close()


if __name__ == "__main__":
    main()
