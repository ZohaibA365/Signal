"""
Keep the warehouse inside its size limit.

Description text is the whole problem: 240 MB of the 489 MB used, against a
512 MB ceiling. The pipeline stopped being able to load new postings at all -
"could not extend file because project size limit has been exceeded" - which
failed the load step and skipped the seven steps after it.

Descriptions are working storage, not a record. Nothing displays them: the
site excludes them from the search payload deliberately, and only two jobs
ever read them - technology extraction and LLM scoring. Once a posting has
been through extraction, its text has done the work it was fetched for, and
S3 still holds the original if it is ever wanted again.

So descriptions are dropped for postings past a retention window, oldest
first, and only where extraction has already run. Rows themselves are kept:
they carry the title, company, location and link the site actually uses, and
those are small.

Usage:
    python storage/prune.py                 # report only
    python storage/prune.py --apply
    python storage/prune.py --apply --target-mb 380
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("prune")

# Windows tried in order, widest first. The pipeline prunes only as far as it
# needs to reach the target, so a quiet week keeps more history than a busy one.
WINDOWS = (180, 120, 90, 60, 45, 30, 21, 14)


def db_mb(cur) -> float:
    cur.execute("SELECT pg_database_size(current_database()) / 1048576.0")
    return float(cur.fetchone()[0])


def main() -> None:
    ap = argparse.ArgumentParser(description="Keep the warehouse inside its size limit")
    ap.add_argument("--apply", action="store_true", help="actually clear text")
    ap.add_argument("--target-mb", type=float, default=380.0,
                    help="stop pruning once the database is under this size")
    args = ap.parse_args()

    conn = connect(autocommit=True)
    try:
        with conn.cursor() as cur:
            size = db_mb(cur)
            log.info("database %.0f MB, target %.0f MB", size, args.target_mb)
            if size <= args.target_mb:
                log.info("under target - nothing to prune")
                return

            for days in WINDOWS:
                cur.execute("""
                    SELECT count(*), coalesce(sum(length(description_raw)), 0) / 1048576.0
                    FROM raw_postings r
                    WHERE r.description_raw IS NOT NULL
                      AND r.posted_date < now() - make_interval(days => %s)
                      AND EXISTS (SELECT 1 FROM posting_technologies p
                                  WHERE p.source = r.source AND p.job_id = r.job_id)
                """, (days,))
                n, mb = cur.fetchone()
                log.info("  older than %3d days: %6d postings, %.0f MB of text", days, n, mb)
                if not n:
                    continue
                if not args.apply:
                    continue

                cur.execute("""
                    UPDATE raw_postings r SET description_raw = NULL
                    WHERE r.description_raw IS NOT NULL
                      AND r.posted_date < now() - make_interval(days => %s)
                      AND EXISTS (SELECT 1 FROM posting_technologies p
                                  WHERE p.source = r.source AND p.job_id = r.job_id)
                """, (days,))
                log.info("    cleared %s descriptions", f"{cur.rowcount:,}")

                # Plain VACUUM, not FULL. FULL rewrites the table and needs as
                # much free space again as the table occupies, which is exactly
                # what is missing here. Plain VACUUM marks the pages reusable,
                # so the next load fills them instead of extending the file -
                # which is what the size limit actually objects to.
                cur.execute("VACUUM (ANALYZE) raw_postings")
                size = db_mb(cur)
                log.info("    database now %.0f MB", size)
                if size <= args.target_mb:
                    break

            log.info("finished at %.0f MB", db_mb(cur))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
