"""
Keep the warehouse inside its size limit.

Description text is the whole problem: 240 MB of the 489 MB used, against a
512 MB ceiling. The pipeline stopped being able to load new postings at all -
"could not extend file because project size limit has been exceeded" - which
failed the load step and skipped the seven steps after it.

The original rule deletes whole rows, and only when a posting is both older than
the retention window and no longer being seen on any board - a live posting is
re-seen on every ingest, so last_seen is what separates "delisted" from "still
open, just posted a while ago".

That rule alone frees almost nothing, and saying so is the reason the second
stage exists: on 2026-09-09 it matched 0 postings at the 180-day window and 13
at 30 days, because the corpus is young and nearly everything in it is still
being re-seen every morning. The database sat at 415 MB regardless, 208 MB of
it description text.

So there are two stages, and they are different kinds of thing.

**Text retention** runs every time, because it is a rule rather than a
response. A description is kept while anything still needs it - the first week
after the posting arrived, plus any intern or entry role the scorer has not
reached yet - and dropped afterwards, but only once S3 provably holds that exact
text. Measured on 2026-09-12: 308 MB of text, of which the retention rule keeps
45 MB. The text is not lost, it moves: storage/archive_postings.py has it keyed
by its own sha256, and the two jobs that read full text finish with a posting
permanently, while the thing the site needs from it every day - the sponsorship
verdict - is now a stored boolean.

**Row deletion** runs only when the database is over target, because deleting a
posting loses the row itself and not just a copy of its text.

Usage:
    python storage/prune.py                 # report only, both stages
    python storage/prune.py --apply
    python storage/prune.py --apply --target-mb 380
    python storage/prune.py --apply --keep-days 14
    python storage/prune.py --apply --skip-text     # when S3 is unreachable
    python storage/prune.py --apply --reclaim       # and hand the space back
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from archive_postings import SHA, load_manifest  # noqa: E402
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

# Why clearing text is batched, and why it was once banned outright.
#
# The first version of this cleared descriptions with one UPDATE, and that is
# what filled the database it was written to protect. Postgres writes a new row
# version per update and marks the old one dead, so clearing 62,993 descriptions
# left 186 MB of dead TOAST that plain VACUUM makes reusable but never returns to
# the operating system. The table sat at 263 MB of TOAST holding 77 MB of live
# text, and because UPDATE needs space before it frees any, the prune eventually
# could not run at all - the fix blocked by what the fix had done. Recovering it
# needed a dump, a TRUNCATE and a restore.
#
# So the rule became "DELETE the row, never UPDATE the text to NULL", and that
# stood until two things changed. S3 now holds every description keyed by its own
# hash, so clearing text loses nothing; and the only reader the site depends on
# daily - the sponsorship verdict - is a stored boolean rather than a regex over
# the text. What remains is the mechanical hazard above, and that is a question
# of batch size: TEXT_BATCH rows at a time with a VACUUM between batches keeps
# the peak at one batch's worth instead of the whole table's.


# Text is kept for one week after a posting first arrived. Everything that reads
# full description text does so within hours of arrival - technology extraction
# and the scorer both run in the same pipeline - and a week is six more days of
# slack than either needs. The scorer's own ordering is the evidence: it takes
# the freshest postings first, so a posting ten days old has already been passed
# over by every run since it arrived.
KEEP_DAYS = 7

# Rows per UPDATE. Small deliberately: the first version of this cleared 62,993
# descriptions in one statement, which wrote 186 MB of dead TOAST that plain
# VACUUM could make reusable but never return, and left the prune unable to run
# at all because UPDATE needs space before it frees any. Batching with a VACUUM
# between batches keeps the peak at one batch's worth.
TEXT_BATCH = 2_000
VACUUM_EVERY = 5

# Which postings keep their text, as a SQL predicate over raw_postings r.
#
# The second clause deliberately does not apply the scorer's own title-relevance
# regex, even though the scorer does. Matching it here would mean prune had to
# import ai_layer and stay in step with a pattern that changes for reasons
# nothing to do with retention; leaving it out keeps a few hundred extra
# descriptions, which is the cheap side of the mistake to make.
KEEP_TEXT = """
    r.first_seen > now() - make_interval(days => %(keep_days)s)
    OR (
        EXISTS (SELECT 1 FROM ranked_opportunities o
                 WHERE o.source = r.source AND o.job_id = r.job_id
                   AND o.link_tier = 'direct'
                   AND o.seniority IN ('intern', 'entry'))
        AND NOT EXISTS (SELECT 1 FROM job_enrichment e
                         WHERE e.source = r.source AND e.job_id = r.job_id)
    )
"""


def archived_text(cur, bucket: str) -> int:
    """
    Load what S3 holds into a temp table, so the drop can be a join.

    The manifest is the archive's own record, read from the archive rather than
    from Postgres - keeping it in Postgres cost 18 MB, which is the headroom this
    whole exercise exists to create, spent on bookkeeping about the thing meant
    to create it.
    """
    import boto3  # local: a --skip-text run must not need the dependency

    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    manifest = load_manifest(s3, bucket)
    # Dropped first, not merely created. A temp table is supposed to vanish with
    # the session, but Neon is reached through PgBouncer in transaction mode, where
    # a server connection outlives the client that used it - so a run that died
    # before its cleanup left this table behind, and the next run failed with
    # "relation 'archived_text' already exists" on a table it had just tried to
    # create. That is how the retry of a failed prune failed differently from the
    # prune.
    cur.execute("DROP TABLE IF EXISTS archived_text")
    cur.execute("""
        CREATE TEMP TABLE archived_text (source TEXT, job_id TEXT, sha TEXT)
        ON COMMIT PRESERVE ROWS
    """)
    execute_values(cur, "INSERT INTO archived_text (source, job_id, sha) VALUES %s",
                   sorted(manifest), page_size=5_000)
    # No index. The drop scans every description once anyway, so this is a hash
    # join either way, and an index here is three more megabytes of temporary
    # space inside the budget the whole exercise exists to widen.
    cur.execute("ANALYZE archived_text")
    return len(manifest)


def keep_clause_is_answerable(cur) -> bool:
    """
    Can the keep rule actually be evaluated?

    It reads ranked_opportunities, a dbt model, so prune depends on a build
    having run. When that table is missing or predates a column the clause
    names, the honest answer is not to guess: skipping the drop keeps text that
    could have gone, while evaluating a half-answerable rule would drop text
    that should have stayed. One is a wasted day of headroom, the other is
    text the scorer needed.
    """
    cur.execute("""
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'ranked_opportunities'
          AND column_name IN ('source', 'job_id', 'link_tier', 'seniority')
    """)
    return cur.fetchone()[0] == 4


def drop_text(cur, args) -> tuple[int, float]:
    """
    Clear description text that is out of retention and provably archived.

    Returns the number of postings cleared and the megabytes of text they held.
    """
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        log.warning("S3_BUCKET is not set - skipping text retention entirely. "
                    "Text is only ever dropped once the archive holds it.")
        return 0, 0.0

    if not keep_clause_is_answerable(cur):
        log.warning("ranked_opportunities is missing or out of date, so the "
                    "retention rule cannot tell which roles the scorer still "
                    "needs - keeping all text this run")
        return 0, 0.0

    held = archived_text(cur, bucket)
    log.info("archive holds %s descriptions", f"{held:,}")

    # Candidates are materialised once. Re-deriving them per batch would hash
    # every description in the table on every batch - a full TOAST scan, about
    # fifteen seconds each, twenty-six times over.
    #
    # Empty descriptions are excluded rather than dropped: the archive skips
    # them, so they can never satisfy the membership test, and a zero-length
    # string costs nothing to keep.
    cur.execute("DROP TABLE IF EXISTS to_drop")
    cur.execute(f"""
        CREATE TEMP TABLE to_drop AS
        SELECT r.source, r.job_id, length(r.description_raw) AS bytes
        FROM raw_postings r
        JOIN archived_text a
          ON a.source = r.source AND a.job_id = r.job_id AND a.sha = {SHA}
        WHERE r.description_raw IS NOT NULL
          AND r.description_raw <> ''
          AND NOT ({KEEP_TEXT})
    """, {"keep_days": args.keep_days})
    cur.execute("SELECT count(*), coalesce(sum(bytes), 0) / 1048576.0 FROM to_drop")
    n, mb = cur.fetchone()
    mb = float(mb)
    log.info("text retention: %s postings hold %.0f MB past the %d-day window "
             "and are in the archive", f"{n:,}", mb, args.keep_days)

    # Anything out of retention that S3 does not hold is the case worth naming:
    # the archive is behind, so the text stays and the next run will try again.
    cur.execute(f"""
        SELECT count(*), coalesce(sum(length(r.description_raw)), 0) / 1048576.0
        FROM raw_postings r
        WHERE r.description_raw IS NOT NULL AND r.description_raw <> ''
          AND NOT ({KEEP_TEXT})
          AND NOT EXISTS (SELECT 1 FROM to_drop d
                           WHERE d.source = r.source AND d.job_id = r.job_id)
    """, {"keep_days": args.keep_days})
    unarchived, unarchived_mb = cur.fetchone()
    if unarchived:
        log.warning("%s postings (%.0f MB) are past the window but not in the "
                    "archive - keeping their text until they are",
                    f"{unarchived:,}", float(unarchived_mb))

    if not n or not args.apply:
        return 0, mb

    cleared = 0
    batches = 0
    while True:
        # Dropping the batch from to_drop as it is applied is what makes the loop
        # terminate: the candidate set shrinks whether or not the UPDATE matched.
        cur.execute("""
            WITH batch AS (
                DELETE FROM to_drop
                WHERE ctid IN (SELECT ctid FROM to_drop LIMIT %s)
                RETURNING source, job_id
            )
            UPDATE raw_postings r
               SET description_raw = NULL,
                   description_dropped_at = now()
              FROM batch b
             WHERE r.source = b.source AND r.job_id = b.job_id
        """, (TEXT_BATCH,))
        if not cur.rowcount:
            break
        cleared += cur.rowcount
        batches += 1
        # Plain VACUUM, not FULL. FULL rewrites the table and needs as much free
        # space again as the table occupies, which is what is missing when this
        # matters. Plain VACUUM marks the dead TOAST pages reusable, so the next
        # load fills them instead of extending the file - and it is extending the
        # file that Neon's size limit objects to.
        if batches % VACUUM_EVERY == 0:
            cur.execute("VACUUM raw_postings")
            log.info("  cleared %s of %s", f"{cleared:,}", f"{n:,}")

    cur.execute("VACUUM (ANALYZE) raw_postings")
    log.info("text retention: cleared %s postings, %.0f MB of text", f"{cleared:,}", mb)
    return cleared, mb


def reclaim(cur) -> float:
    """
    Return the emptied pages to Neon's meter, and say so in megabytes.

    Clearing text with UPDATE does not shrink anything by itself. Postgres marks
    the old row versions dead and plain VACUUM makes those pages reusable, which
    is what stops the file growing - but the file keeps its size, and Neon meters
    the file. Turning 250 MB of reusable pages back into headroom needs a rewrite.

    Which thing gets rewritten depends on the room available, because VACUUM FULL
    writes a second copy before dropping the first:

      - The whole table, when the headroom comfortably exceeds its current total
        size. Best result, since it compacts the heap and the indexes too, and
        rewrites the text store as part of the same operation.
      - The text store alone otherwise. That is where the space usually is - 208 MB
        of a 286 MB table on the morning this was written - and the live text left
        after retention is a fraction of the whole.
      - Nothing, when even that does not fit, saying so rather than trying. This is
        the case that matters: the first version of text-clearing filled the
        database it was protecting, and the prune could then not run at all.

    The size used for the test is the relation's total size rather than its live
    bytes, which over-estimates what the copy needs - erring towards not starting.

    Measured on Neon: with 95 MB of headroom it rewrote the text store and returned
    171 MB, taking the database from 417 MB to 246 MB.
    """
    cur.execute("""
        SELECT c.reltoastrelid::regclass::text,
               pg_total_relation_size(c.reltoastrelid) / 1048576.0,
               pg_total_relation_size(c.oid) / 1048576.0,
               pg_database_size(current_database()) / 1048576.0
        FROM pg_class c WHERE c.oid = 'raw_postings'::regclass
    """)
    row = cur.fetchone()
    toast = row[0]
    toast_mb, table_mb, db_before = (float(v) for v in row[1:])
    headroom = CEILING_MB - db_before

    if table_mb * 1.2 <= headroom:
        target, size_mb, what = "raw_postings", table_mb, "the whole table"
    elif toast_mb * 1.2 <= headroom:
        target, size_mb, what = toast, toast_mb, "the text store"
    else:
        log.warning("skipping reclaim: the smallest rewrite available needs %.0f MB "
                    "and only %.0f MB of headroom is left. The pages are reusable, "
                    "so loads will not grow the file - but Neon still meters it.",
                    toast_mb * 1.2, headroom)
        return 0.0

    log.info("rewriting %s (%s, %.0f MB) with %.0f MB of headroom",
             target, what, size_mb, headroom)
    cur.execute(f"VACUUM FULL {target}")
    cur.execute("ANALYZE raw_postings")
    freed = db_before - db_mb(cur)
    log.info("reclaim returned %.0f MB", freed)
    return freed


def forget_temp_tables(cur) -> None:
    """
    Drop the working tables before anything measures the database again.

    A temp table is still storage. The manifest copy is about fifteen megabytes,
    and leaving it in place while report() reads pg_database_size made a dry run
    look as though the prune had cost eighteen megabytes rather than freeing any.
    """
    cur.execute("DROP TABLE IF EXISTS to_drop")
    cur.execute("DROP TABLE IF EXISTS archived_text")


def db_mb(cur) -> float:
    cur.execute("SELECT pg_database_size(current_database()) / 1048576.0")
    return float(cur.fetchone()[0])


def report(cur, started_mb: float, cleared: int, deleted: int, args) -> None:
    """
    Say plainly whether the prune actually did anything.

    It used to log a size and exit 0 whether it had freed 200 MB or nothing,
    and "nothing" is the case that matters: the row rule is old AND delisted,
    but a posting that is still on a board keeps being re-seen, so last_seen
    never goes stale for anything recent. On 2026-09-09 that rule matched 0 rows
    at the 180-day window and 13 at 30 days, against a database sitting at
    415 MB of a 512 MB ceiling. The run looked like a success every morning
    while the ceiling got closer.

    A warning annotation is used rather than a failure because a full
    warehouse is not this step's fault and failing here would skip the eight
    steps after it - which is the outage, not the fix.
    """
    final_mb = db_mb(cur)
    freed = started_mb - final_mb
    log.info("finished at %.0f MB - cleared text on %s postings, deleted %s, "
             "reclaimed %.0f MB", final_mb, f"{cleared:,}", f"{deleted:,}", freed)

    if final_mb <= args.target_mb:
        return

    headroom = CEILING_MB - final_mb
    log.warning("still %.0f MB over target with only %.0f MB of headroom left",
                final_mb - args.target_mb, headroom)
    if not cleared and not deleted:
        log.warning("neither stage freed anything: text is inside the retention "
                    "window, and postings are still being re-seen so last_seen "
                    "never goes stale")

    # GitHub renders these on the run page even for people who cannot read
    # step logs, so the ceiling shows up before it becomes an outage.
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::warning::Warehouse at {final_mb:.0f} MB of {CEILING_MB:.0f} MB "
              f"({headroom:.0f} MB headroom). Prune reclaimed {freed:.0f} MB - "
              f"text cleared on {cleared:,} postings, {deleted:,} deleted.")
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                fh.write(f"\n**Warehouse {final_mb:.0f} MB / {CEILING_MB:.0f} MB** "
                         f"- reclaimed {freed:.0f} MB: text cleared on "
                         f"{cleared:,} postings, {deleted:,} deleted, "
                         f"{headroom:.0f} MB headroom remaining.\n")


def delete_old_postings(cur, args) -> int:
    """
    Delete whole postings, widest retention window first, until under target.

    Unchanged in what it does and still deliberately conservative: a posting
    qualifies only when it is both older than the window and no longer being
    seen on any board. It is now the second stage rather than the only one,
    which is what lets it stay conservative - text retention does the volume.
    """
    deleted = 0
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
        if not n or not args.apply:
            continue

        # Only postings that are BOTH old and no longer being seen on any
        # board. A live posting keeps being re-seen every ingest, so last_seen
        # is what distinguishes "still open, just posted a while ago" from
        # "delisted".
        cur.execute("""
            DELETE FROM raw_postings r
            WHERE r.posted_date < now() - make_interval(days => %s)
              AND r.last_seen < now() - interval '7 days'
        """, (days,))
        deleted += cur.rowcount
        log.info("    deleted %s delisted postings", f"{cur.rowcount:,}")

        # Plain VACUUM, not FULL. FULL rewrites the table and needs as much
        # free space again as the table occupies, which is exactly what is
        # missing when this fires. Plain VACUUM marks the pages reusable so the
        # next load fills them instead of extending the file, which is what the
        # size limit objects to.
        cur.execute("VACUUM (ANALYZE) raw_postings")
        size = db_mb(cur)
        log.info("    database now %.0f MB", size)
        if size <= args.target_mb:
            break
    return deleted


def main() -> None:
    ap = argparse.ArgumentParser(description="Keep the warehouse inside its size limit")
    ap.add_argument("--apply", action="store_true",
                    help="actually clear text and delete postings")
    ap.add_argument("--target-mb", type=float, default=380.0,
                    help="delete whole postings until the database is under this size")
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS,
                    help="keep description text this many days after a posting arrived")
    ap.add_argument("--skip-text", action="store_true",
                    help="skip text retention, e.g. when S3 is unreachable")
    ap.add_argument("--reclaim", action="store_true",
                    help="rewrite the description store so the freed space is "
                         "returned rather than only marked reusable")
    args = ap.parse_args()

    # direct=True, not the pooled endpoint. This run creates temporary tables and
    # runs VACUUM, and both need every statement on the same server session -
    # through PgBouncer in transaction mode a temp table vanishes between
    # statements, intermittently, depending on which backend the pool hands out.
    conn = connect(autocommit=True, direct=True)
    try:
        with conn.cursor() as cur:
            size = started_mb = db_mb(cur)
            log.info("database %.0f MB of %.0f MB ceiling, target %.0f MB",
                     size, CEILING_MB, args.target_mb)

            # Stage one, unconditional. Retention is a rule about how long text
            # is worth keeping, not a response to an emergency - a rule that
            # only fires near the ceiling is how the warehouse spent a
            # fortnight sitting at 415 MB.
            cleared = 0
            if not args.skip_text:
                # try/finally, because the working tables have to go even when the
                # drop fails. They did not, once, and the next run inherited them
                # through the connection pooler.
                try:
                    cleared, _ = drop_text(cur, args)
                finally:
                    forget_temp_tables(cur)
                if cleared:
                    size = db_mb(cur)
                    log.info("database %.0f MB after text retention", size)

            # Separate from the clearing, and not conditional on it: pages freed
            # by an earlier run are still only reusable, so the rewrite has
            # something to do on a day when retention clears nothing.
            if args.reclaim and args.apply:
                reclaim(cur)
                size = db_mb(cur)

            # Stage two, only when still over target. This loses the posting
            # itself rather than a copy of its text, so it needs a reason.
            deleted = 0
            if size > args.target_mb:
                deleted = delete_old_postings(cur, args)
            else:
                log.info("under target - no postings need deleting")

            report(cur, started_mb, cleared, deleted, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
