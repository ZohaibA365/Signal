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
    # Paged with a keyset, not fetched in one go.
    #
    # 30,910 unmatched postings carry about 155 MB of description text between
    # them, and asking for all of it in a single statement timed the
    # connection out - "SSL SYSCALL error: Operation timed out" - after the
    # query had already done the work. Pages are read on a short-lived
    # connection and written on another, because the matching in between runs
    # for minutes and Neon closes anything left idle across it.
    #
    # The cursor is (source, job_id) rather than an offset. A posting that
    # matches no technology never gets a row in posting_technologies, so a
    # NOT EXISTS filter would hand it back on every page forever; a keyset
    # moves past it.
    PAGE = 2000
    last_source, last_job = "", ""
    rows_seen = 0
    pairs_written = 0
    matched_postings = 0

    if args.rebuild:
        conn = connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("TRUNCATE posting_technologies")
        finally:
            conn.close()
        log.info("Cleared existing matches (--rebuild)")

    while True:
        conn = connect()
        try:
            with conn.cursor() as cur:
                # Match the title as well as the description: the title often
                # names the technology ("Snowflake Data Engineer") when a
                # truncated description does not.
                cur.execute("""
                    SELECT r.source, r.job_id,
                           coalesce(r.job_title,'') || ' ' || coalesce(r.description_raw,'')
                    FROM raw_postings r
                    WHERE (r.source, r.job_id) > (%s, %s)
                      AND (%s OR NOT EXISTS (
                          SELECT 1 FROM posting_technologies p
                          WHERE p.source = r.source AND p.job_id = r.job_id
                      ))
                    ORDER BY r.source, r.job_id
                    LIMIT %s
                """, (last_source, last_job, args.rebuild, PAGE))
                page = cur.fetchall()
        finally:
            conn.close()

        if not page:
            break
        last_source, last_job = page[-1][0], page[-1][1]
        rows_seen += len(page)

        pairs: list[tuple] = []
        for source, job_id, text in page:
            slugs = match_technologies(text)
            if slugs:
                matched_postings += 1
                pairs.extend((source, job_id, s) for s in slugs)

        for i in range(0, len(pairs), BATCH):
            conn = connect()
            try:
                with conn, conn.cursor() as cur:
                    execute_values(cur, """
                        INSERT INTO posting_technologies (source, job_id, tech_slug)
                        VALUES %s ON CONFLICT DO NOTHING
                    """, pairs[i:i + BATCH], page_size=1000)
            finally:
                conn.close()
        pairs_written += len(pairs)
        log.info("  %s postings scanned, %s mentions written",
                 f"{rows_seen:,}", f"{pairs_written:,}")

    pct = (matched_postings / rows_seen * 100) if rows_seen else 0
    log.info("%s of %s postings matched at least one technology (%.0f%%), "
             "%s mentions written",
             f"{matched_postings:,}", f"{rows_seen:,}", pct, f"{pairs_written:,}")

    conn.close()


if __name__ == "__main__":
    main()
