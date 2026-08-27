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

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(__file__))
from taxonomy import BY_SLUG, match_technologies  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("extract_tech")

BATCH = 5000


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract technology mentions from postings")
    ap.add_argument("--rebuild", action="store_true",
                    help="clear existing matches and re-match every posting")
    args = ap.parse_args()

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"), port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"), user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )

    with conn, conn.cursor() as cur:
        if args.rebuild:
            cur.execute("TRUNCATE posting_technologies")
            log.info("Cleared existing matches (--rebuild)")

        # Match the title as well as the description: the title often names
        # the technology ("Snowflake Data Engineer") when the truncated
        # description does not.
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
        log.info("%s postings to match against %s technologies", f"{len(rows):,}", len(BY_SLUG))

        pairs: list[tuple] = []
        matched_postings = 0
        for source, job_id, text in rows:
            slugs = match_technologies(text)
            if slugs:
                matched_postings += 1
                pairs.extend((source, job_id, s) for s in slugs)

        for i in range(0, len(pairs), BATCH):
            execute_values(cur, """
                INSERT INTO posting_technologies (source, job_id, tech_slug)
                VALUES %s ON CONFLICT DO NOTHING
            """, pairs[i:i + BATCH], page_size=1000)

        pct = (matched_postings / len(rows) * 100) if rows else 0
        log.info("%s postings matched at least one technology (%.0f%%), %s mentions total",
                 f"{matched_postings:,}", pct, f"{len(pairs):,}")

    conn.close()


if __name__ == "__main__":
    main()
