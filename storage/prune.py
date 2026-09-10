"""
Keep the warehouse inside its size limit.

Description text is the whole problem: 240 MB of the 489 MB used, against a
512 MB ceiling. The pipeline stopped being able to load new postings at all -
"could not extend file because project size limit has been exceeded" - which
failed the load step and skipped the seven steps after it.

Whole rows are deleted, not description text - see the long comment below
for why the text-clearing version had to be abandoned. A posting qualifies
only when it is both older than the retention window and no longer being seen
on any board, because a live posting is re-seen on every ingest.

Know the limitation before relying on this. That rule is deliberately
conservative and, at the moment, it frees almost nothing: on 2026-09-09 it
matched 0 postings at the 180-day window and 13 at 30 days, because the
corpus is young and nearly everything in it is still being re-seen. The
database sat at 415 MB of a 512 MB ceiling regardless, with 195 MB of that in
the description TOAST table.

So this script cannot currently keep the warehouse under its limit, and it
now says so - report() emits a warning annotation rather than logging a size
and exiting 0. The real fix is to stop storing finished description text in
the serving database at all, which needs the archive to exist first.

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
WINDOWS = (180, 120, 90, 60, 45, 30)

# Neon's free tier ceiling. Only used for reporting: the point is that a run
# which frees nothing should say so loudly rather than logging a size and
# exiting 0, which is what it did while the database sat at 415 MB for days.
CEILING_MB = 512.0

# DELETE the row, never UPDATE the text to NULL.
#
# The first version of this cleared descriptions with UPDATE, and that is what
# filled the database it was written to protect. Postgres writes a new row
# version per update and marks the old one dead, so clearing 62,993
# descriptions left 186 MB of dead TOAST that plain VACUUM makes reusable but
# never returns to the operating system. The table sat at 263 MB of TOAST
# holding 77 MB of live text, and because UPDATE also needs space before it
# frees any, the prune eventually could not run at all - the fix blocked by
# what the fix had done. Recovering it needed a dump, a TRUNCATE and a
# restore.
#
# Deleting the row is cheaper: it marks the tuple dead without writing a
# second copy of the text first, so it works even when space is tight. And a
# posting old enough to prune is not on the site anyway - the search payload
# already stops at the freshest 20,000 - while S3 keeps every original.


def db_mb(cur) -> float:
    cur.execute("SELECT pg_database_size(current_database()) / 1048576.0")
    return float(cur.fetchone()[0])


def report(cur, started_mb: float, deleted: int, args) -> None:
    """
    Say plainly whether the prune actually did anything.

    It used to log a size and exit 0 whether it had freed 200 MB or nothing,
    and "nothing" is the case that matters: the retention rule is old AND
    delisted, but a posting that is still on a board keeps being re-seen, so
    last_seen never goes stale for anything recent. On 2026-09-09 that rule
    matched 0 rows at the 180-day window and 13 at 30 days, against a database
    sitting at 415 MB of a 512 MB ceiling. The run looked like a success every
    morning while the ceiling got closer.

    A warning annotation is used rather than a failure because a full
    warehouse is not this step's fault and failing here would skip the eight
    steps after it - which is the outage, not the fix.
    """
    final_mb = db_mb(cur)
    freed = started_mb - final_mb
    log.info("finished at %.0f MB - deleted %s postings, reclaimed %.0f MB",
             final_mb, f"{deleted:,}", freed)

    if final_mb <= args.target_mb:
        return

    headroom = CEILING_MB - final_mb
    log.warning("still %.0f MB over target with only %.0f MB of headroom left",
                final_mb - args.target_mb, headroom)
    if not deleted:
        log.warning("the retention rule matched nothing: postings are still "
                    "being re-seen, so last_seen never goes stale")

    # GitHub renders these on the run page even for people who cannot read
    # step logs, so the ceiling shows up before it becomes an outage.
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::warning::Warehouse at {final_mb:.0f} MB of {CEILING_MB:.0f} MB "
              f"({headroom:.0f} MB headroom). Prune reclaimed {freed:.0f} MB "
              f"from {deleted:,} postings.")
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                fh.write(f"\n**Warehouse {final_mb:.0f} MB / {CEILING_MB:.0f} MB** "
                         f"- reclaimed {freed:.0f} MB from {deleted:,} postings, "
                         f"{headroom:.0f} MB headroom remaining.\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Keep the warehouse inside its size limit")
    ap.add_argument("--apply", action="store_true", help="actually delete postings")
    ap.add_argument("--target-mb", type=float, default=380.0,
                    help="stop pruning once the database is under this size")
    args = ap.parse_args()

    conn = connect(autocommit=True)
    try:
        with conn.cursor() as cur:
            size = started_mb = db_mb(cur)
            deleted = 0
            log.info("database %.0f MB of %.0f MB ceiling, target %.0f MB",
                     size, CEILING_MB, args.target_mb)
            if size <= args.target_mb:
                log.info("under target - nothing to prune")
                return

            for days in WINDOWS:
                cur.execute("""
                    SELECT count(*), coalesce(sum(length(coalesce(description_raw,''))), 0)
                                     / 1048576.0
                    FROM raw_postings r
                    WHERE r.posted_date < now() - make_interval(days => %s)
                      AND r.last_seen < now() - interval '7 days'
                """, (days,))
                n, mb = cur.fetchone()
                log.info("  older than %3d days and delisted: %6d postings, %.0f MB",
                         days, n, mb)
                if not n:
                    continue
                if not args.apply:
                    continue

                # Only postings that are BOTH old and no longer being seen on
                # any board. A live posting keeps being re-seen every ingest,
                # so last_seen is what distinguishes "still open, just posted a
                # while ago" from "delisted".
                cur.execute("""
                    DELETE FROM raw_postings r
                    WHERE r.posted_date < now() - make_interval(days => %s)
                      AND r.last_seen < now() - interval '7 days'
                """, (days,))
                deleted += cur.rowcount
                log.info("    deleted %s delisted postings", f"{cur.rowcount:,}")

                # Plain VACUUM, not FULL. FULL rewrites the table and needs as
                # much free space again as the table occupies, which is exactly
                # what is missing when this fires. Plain VACUUM marks the pages
                # reusable so the next load fills them instead of extending the
                # file, which is what the size limit objects to.
                cur.execute("VACUUM (ANALYZE) raw_postings")
                size = db_mb(cur)
                log.info("    database now %.0f MB", size)
                if size <= args.target_mb:
                    break

            report(cur, started_mb, deleted, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
