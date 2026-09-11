"""
Append-only archive of the posting corpus in S3.

The warehouse forgets, in three separate ways. `raw_postings` is upserted in
place, so today's `last_seen` overwrites yesterday's and no record survives
that a posting was open on any particular day. `prune.py` deletes rows
outright. And `export_parquet.py` rewrites `data.parquet` per partition, so
even the lake's curated layer is a snapshot rather than a record.

The cost of that showed up on 2026-09-09: the whole corpus held 9 distinct
collection days, and both `MIN_DAYS_FOR_TREND` gates - which need 45 - were
still shut, so every trend claim on the site and in outreach was suppressed.
Meanwhile `market_demand.sql` computes `pct_change_7d`, `pct_change_30d` and
`has_trend` that nothing is able to read.

This writes three things, each to a date-partitioned key that is never
rewritten:

  presence/      (source, job_id) observed on a given day. One row per posting
                 per day, which is the object Neon structurally cannot hold.
  postings/      Attributes for postings first seen on a given day. Written
                 once: attributes rarely change, and presence already carries
                 the daily signal.
  descriptions/  Text keyed by its sha256, written only when that exact text
                 has not been archived before.

Descriptions are the expensive part - 195 MB of TOAST in Neon, 50,091
distinct texts averaging 5,236 characters - so they are deduplicated against
a manifest of keys and hashes already written. That manifest lives in S3
under the same prefix, not in Postgres: the first version put it in Neon and
it cost 18 MB, spending the headroom this exercise exists to create. Without
some manifest every run would re-upload the whole corpus, because `last_seen`
moves for all ~54,000 postings every morning whether or not text changed.

Nothing here depends on Snowflake. S3 is the durable copy and costs a few
cents a month; Snowflake, when configured, is a query engine over the same
facts and can disappear without taking the archive with it.

Usage:
    python storage/archive_postings.py                  # the latest ingest day
    python storage/archive_postings.py --backfill       # all known history, once
    python storage/archive_postings.py --date 2026-09-09
    python storage/archive_postings.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from datetime import UTC, datetime

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect, describe  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("archive")

PREFIX = "archive"

# Text is fetched in batches rather than all at once: 50,091 descriptions
# averaging 5,236 characters is roughly 260 MB, which is fine on disk and
# careless to hold in a runner's memory alongside pyarrow's copy of it.
BATCH = 2_000

# Full 64-character hex, where enrich.py uses the first 16 for its
# change-detection key. Storing the full digest keeps that compatible - its
# hash is a prefix of this one - while giving the archive enough bits that
# collisions stay theoretical as the corpus grows.
SHA = "encode(sha256(convert_to(description_raw,'UTF8')),'hex')"

# Everything except description_raw. The text is archived separately and
# deduplicated, so carrying it here too would store it twice.
ATTRS = [
    "source", "job_id", "country", "company_name", "job_title", "location",
    "posted_date", "salary_min", "salary_max", "salary_is_predicted",
    "category", "redirect_url", "latitude", "longitude", "location_state",
    "search_term", "ingested_at", "first_seen", "last_seen",
]


def latest_ingest_day(cur) -> str:
    """
    The day the most recent load actually ran, not "today".

    current_date is evaluated in the session's timezone, which on Neon is UTC.
    Running this at 20:44 US Eastern means the database already believes it is
    tomorrow, so anchoring on current_date archives nothing at all - measured,
    not hypothetical. The newest last_seen is the honest anchor: it is the day
    the ingest that produced these rows completed.
    """
    cur.execute("SELECT max(last_seen)::date FROM raw_postings")
    day = cur.fetchone()[0]
    if day is None:
        raise SystemExit("raw_postings is empty - nothing to archive")
    return day.isoformat()


def put(s3, bucket: str, key: str, rows: list[dict], dry: bool) -> int:
    """Write one Parquet object. Returns bytes written."""
    if not rows:
        return 0
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    body = buf.getvalue()
    if dry:
        log.info("    [dry-run] would write %6s rows, %5.1f MB -> s3://%s/%s",
                 f"{len(rows):,}", len(body) / 1e6, bucket, key)
        return len(body)
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    log.info("    %6s rows, %5.1f MB -> s3://%s/%s",
             f"{len(rows):,}", len(body) / 1e6, bucket, key)
    return len(body)


def archive_presence(cur, s3, bucket: str, day: str, backfill: bool, dry: bool) -> int:
    """
    One row per posting per day it was actually observed.

    Both first_seen and last_seen are real sightings, so both are recorded.
    What is *not* recorded is the days between them: raw_postings holds only
    those two timestamps, so for a posting first seen on the 23rd and last
    seen on the 30th the warehouse genuinely does not know about the 27th.
    Writing those days anyway would manufacture coverage that was never
    collected, and this project has already published one false trend claim
    built on exactly that kind of inference.

    The two are deduplicated to one row per posting-day. Without that, a
    posting first seen and last seen on the same day appears twice and every
    "roles open on day D" count is inflated - the backfill wrote 55,602 rows
    for 2026-09-09 where only 53,771 postings existed, because 1,831 of them
    were counted under both labels. Deduplicating also makes a backfill and a
    daily run produce byte-identical output for the same day, which is what
    makes re-running one safe.
    """
    where = "" if backfill else "WHERE observed_date = %s::date"
    cur.execute(f"""
        WITH observations AS (
            SELECT source, job_id, first_seen::date AS observed_date,
                   'first_seen' AS observed_via
            FROM raw_postings
            UNION ALL
            SELECT source, job_id, last_seen::date, 'last_seen'
            FROM raw_postings
        )
        SELECT DISTINCT ON (source, job_id, observed_date)
               source, job_id, observed_date::text, observed_via
        FROM observations
        {where}
        ORDER BY source, job_id, observed_date, observed_via
    """, () if backfill else (day,))

    # Column names are stated rather than read from cur.description: a
    # server-side cursor leaves that None until the first fetch.
    cols = ["source", "job_id", "observed_date", "observed_via"]
    by_day: dict[str, list[dict]] = {}
    for row in cur:
        rec = dict(zip(cols, row, strict=True))
        by_day.setdefault(rec["observed_date"], []).append(rec)

    if not by_day:
        log.warning("  presence: nothing observed on %s", day)
        return 0

    written = 0
    for d, rows in sorted(by_day.items()):
        written += put(s3, bucket, f"{PREFIX}/presence/observed_date={d}/data.parquet",
                       rows, dry)
    log.info("  presence: %s rows across %d day(s)",
             f"{sum(len(r) for r in by_day.values()):,}", len(by_day))
    return written


def archive_attributes(cur, s3, bucket: str, day: str, backfill: bool, dry: bool) -> int:
    """Attributes for postings first seen on the day, partitioned by that day."""
    where = "" if backfill else "WHERE first_seen::date = %s::date"
    cur.execute(f"""
        SELECT {", ".join(ATTRS)}, first_seen::date::text AS first_seen_date
        FROM raw_postings {where}
    """, () if backfill else (day,))

    cols = [*ATTRS, "first_seen_date"]
    by_day: dict[str, list[dict]] = {}
    for row in cur:
        rec = dict(zip(cols, row, strict=True))
        # Parquet has no numeric/timestamptz equivalent that survives
        # round-tripping through pylist cleanly, so anything exotic becomes a
        # string. The archive's job is fidelity of content, not of type.
        for k, v in rec.items():
            if v is not None and not isinstance(v, (str, int, float, bool)):
                rec[k] = str(v)
        by_day.setdefault(rec["first_seen_date"], []).append(rec)

    if not by_day:
        log.info("  attributes: no postings first seen on %s", day)
        return 0

    written = 0
    for d, rows in sorted(by_day.items()):
        written += put(s3, bucket, f"{PREFIX}/postings/first_seen_date={d}/data.parquet",
                       rows, dry)
    log.info("  attributes: %s rows across %d day(s)",
             f"{sum(len(r) for r in by_day.values()):,}", len(by_day))
    return written


def load_manifest(s3, bucket: str) -> set[tuple[str, str, str]]:
    """
    What the archive already holds, read from the archive itself.

    This deliberately does not live in Postgres. The first version put it
    there and it cost 18 MB, taking the warehouse from 415 MB to 433 MB of a
    512 MB ceiling - spending the headroom this whole exercise exists to
    create, on bookkeeping about the thing that was supposed to relieve it.
    Archive metadata belongs with the archive.
    """
    manifest: set[tuple[str, str, str]] = set()
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": f"{PREFIX}/manifest/"}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            d = pq.read_table(io.BytesIO(body)).to_pydict()
            manifest.update(zip(d["source"], d["job_id"], d["description_sha256"],
                                strict=True))
        if not page.get("IsTruncated"):
            return manifest
        token = page["NextContinuationToken"]


def save_manifest(s3, bucket: str, entries: set, day: str, shard: int, dry: bool) -> None:
    """
    Written in shards during a run, not once at the end.

    A backfill moves 98 MB of text in 30 batches. Writing the manifest only on
    success means a run killed at batch 29 re-uploads all of it; a shard per
    few batches means it resumes.
    """
    rows = [{"source": a, "job_id": b, "description_sha256": c}
            for a, b, c in sorted(entries)]
    put(s3, bucket, f"{PREFIX}/manifest/{day}-{shard:04d}.parquet", rows, dry)


def archive_descriptions(conn, s3, bucket: str, day: str, dry: bool) -> int:
    """
    Text not previously archived, keyed by its own hash.

    Hashing every description is a full scan of the TOAST table - about 15
    seconds against the live warehouse. That is the price of detecting edited
    text without trusting a timestamp, and last_seen cannot be trusted here:
    the loader refreshes it for all ~54,000 postings every morning whether or
    not a character changed.
    """
    known = load_manifest(s3, bucket)
    log.info("  descriptions: archive already holds %s", f"{len(known):,}")

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT source, job_id, {SHA}
            FROM raw_postings
            WHERE description_raw IS NOT NULL AND description_raw <> ''
        """)
        todo = [row for row in cur.fetchall() if tuple(row) not in known]

    if not todo:
        log.info("  descriptions: nothing new to archive")
        return 0
    log.info("  descriptions: %s to archive", f"{len(todo):,}")

    written, added, shard = 0, set(), 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        keys = [(s, j) for s, j, _ in chunk]
        with conn.cursor() as cur:
            # page_size must cover the whole chunk and fetch must be True.
            # execute_values re-executes the statement once per page and, for
            # a SELECT, only the last page's rows are left on the cursor - at
            # the default page size of 100 this silently archived 100 rows per
            # batch instead of 2,000, so 55,100 of 58,013 descriptions would
            # have been skipped while the run reported success.
            rows = [
                {"source": r[0], "job_id": r[1],
                 "description_sha256": r[2], "description_raw": r[3]}
                for r in execute_values(cur, f"""
                    SELECT r.source, r.job_id, {SHA} AS description_sha256,
                           r.description_raw
                    FROM raw_postings r
                    JOIN (VALUES %s) AS k(source, job_id)
                      ON k.source = r.source AND k.job_id = r.job_id
                """, keys, page_size=len(keys), fetch=True)
            ]

        part = i // BATCH
        written += put(s3, bucket,
                       f"{PREFIX}/descriptions/archived_date={day}/part-{part:04d}.parquet",
                       rows, dry)
        added.update((r["source"], r["job_id"], r["description_sha256"]) for r in rows)

        if len(added) >= 10 * BATCH:
            save_manifest(s3, bucket, added, day, shard, dry)
            added, shard = set(), shard + 1

    if added:
        save_manifest(s3, bucket, added, day, shard, dry)
    return written


def write_run_marker(s3, bucket: str, day: str, mode: str, counts: dict,
                     dry: bool) -> None:
    """
    Record that an archive run covered this day, and how.

    Without this the presence panel silently lies about coverage. A backfill
    can only recover first_seen and last_seen, so a posting open from the 23rd
    to the 9th contributes rows for two days and nothing in between - measured
    across the whole corpus that came out as 1.84 observed days per posting
    against a 17-day span. Counting "roles open on the 27th" over that panel
    undercounts by an unknown amount.

    A daily run has no such gap: it records every posting seen that day. So the
    distinction that matters is not row provenance but whether a run of mode
    "daily" ever covered the date, and that is what this marker states. Trend
    work must read it rather than assuming every date in the panel is complete.

    This project has published one false trend claim built on exactly this kind
    of unstated assumption; the marker is cheaper than a third retraction.
    """
    # "mode" is a reserved word in DuckDB, and this file is read by
    # analytics/build_history.py, so the column is run_mode.
    rows = [{"observed_date": day, "run_mode": mode,
             "written_at": datetime.now(UTC).isoformat(), **counts}]
    put(s3, bucket, f"{PREFIX}/runs/observed_date={day}/run.json.parquet", rows, dry)


def main() -> None:
    ap = argparse.ArgumentParser(description="Append-only posting archive in S3")
    ap.add_argument("--date", help="ingest day to archive (default: latest in the warehouse)")
    ap.add_argument("--backfill", action="store_true",
                    help="archive all history the warehouse still holds, once")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--skip-descriptions", action="store_true",
                    help="presence and attributes only - the cheap part")
    args = ap.parse_args()

    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise SystemExit("S3_BUCKET is not set - the archive has nowhere to go")

    conn = connect()
    conn.autocommit = False
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    log.info("Archiving from %s to s3://%s/%s/", describe(), bucket, PREFIX)

    try:
        with conn.cursor() as cur:
            day = args.date or latest_ingest_day(cur)
        log.info("Ingest day: %s%s", day, "  (backfill)" if args.backfill else "")

        total = 0
        with conn.cursor(name="presence") as cur:
            cur.itersize = 10_000
            total += archive_presence(cur, s3, bucket, day, args.backfill, args.dry_run)
        with conn.cursor(name="attrs") as cur:
            cur.itersize = 10_000
            total += archive_attributes(cur, s3, bucket, day, args.backfill, args.dry_run)
        conn.rollback()  # named cursors hold a read transaction open; close it

        if not args.skip_descriptions:
            total += archive_descriptions(conn, s3, bucket, day, args.dry_run)

        # Only a daily run covers a day completely. A backfill reconstructs two
        # sightings per posting and nothing between them, so it must not claim
        # the days it touches are complete.
        if not args.backfill:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM raw_postings WHERE last_seen::date = %s::date",
                            (day,))
                live = cur.fetchone()[0]
            write_run_marker(s3, bucket, day, "daily",
                             {"postings_observed": live}, args.dry_run)

        log.info("Done - %.1f MB written%s", total / 1e6,
                 " (dry run)" if args.dry_run else "")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
