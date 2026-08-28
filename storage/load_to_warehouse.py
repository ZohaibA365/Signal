"""
S3 -> Postgres loader.

Reads the raw Adzuna JSON written by ingestion/adzuna_ingest.py, flattens each
posting into columns, and upserts it into raw_postings.

Idempotency: the upsert keys on (source, job_id). Re-running for the same date
updates existing rows rather than duplicating them, and refreshes last_seen
while preserving first_seen. That makes re-runs and backfills safe, and gives
us the timestamps needed to detect postings that have gone stale.

Usage:
    python storage/load_to_warehouse.py                  # today's partition (UTC)
    python storage/load_to_warehouse.py --date 2026-08-23
    python storage/load_to_warehouse.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import boto3
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("load_to_warehouse")

COLUMNS = [
    "source", "job_id", "country", "company_name", "job_title", "location", "posted_date",
    "salary_min", "salary_max", "salary_is_predicted", "description_raw",
    "category", "redirect_url", "latitude", "longitude", "location_state",
    "location_area", "search_term", "ingested_at", "last_seen",
]

# On conflict, refresh everything that can legitimately change on a repost,
# plus last_seen. first_seen is deliberately absent so the original sighting
# is preserved.
UPSERT = f"""
INSERT INTO raw_postings ({", ".join(COLUMNS)})
VALUES %s
ON CONFLICT (source, job_id) DO UPDATE SET
    company_name        = EXCLUDED.company_name,
    job_title           = EXCLUDED.job_title,
    location            = EXCLUDED.location,
    posted_date         = EXCLUDED.posted_date,
    salary_min          = EXCLUDED.salary_min,
    salary_max          = EXCLUDED.salary_max,
    salary_is_predicted = EXCLUDED.salary_is_predicted,
    description_raw     = EXCLUDED.description_raw,
    category            = EXCLUDED.category,
    redirect_url        = EXCLUDED.redirect_url,
    location_state      = EXCLUDED.location_state,
    location_area       = EXCLUDED.location_area,
    search_term         = EXCLUDED.search_term,
    ingested_at         = EXCLUDED.ingested_at,
    last_seen           = EXCLUDED.last_seen
"""


def _num(value):
    """Adzuna returns numbers as strings or nulls inconsistently."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value):
    """salary_is_predicted arrives as the string '0' or '1'."""
    if value in (None, ""):
        return None
    return str(value) == "1"


def _state(posting: dict):
    """
    Adzuna nests location as ["US", "<state>", "<county>", "<city>"].
    The flat display_name only carries city and county, so the state - the
    field anyone actually filters a job search on - has to come from here.
    """
    area = (posting.get("location") or {}).get("area") or []
    return area[1] if len(area) > 1 else None


def flatten_board(posting: dict, meta: dict, seen_at: str) -> tuple:
    """
    Map one career-board posting (Greenhouse/Lever/Ashby) onto raw_postings.

    Boards give full descriptions rather than Adzuna's 500-character excerpt,
    and no salary, so the salary fields stay null rather than being filled
    with an estimate.
    """
    loc = posting.get("location") or ""
    state = None
    parts = [p.strip() for p in loc.replace("-", ",").split(",") if p.strip()]
    if len(parts) >= 2:
        # "US-CA-Menlo Park" -> CA ; "San Francisco, California" -> California
        state = parts[1] if len(parts[1]) > 1 else parts[-1]

    country = "ca" if "canada" in loc.lower() or loc.strip().upper().startswith("CA-") else "us"

    return (
        "company_board",
        f"{meta.get('board', 'board')}:{posting.get('job_id')}",
        country,
        meta.get("company"),
        posting.get("title"),
        loc,
        posting.get("posted"),
        None, None, None,                      # salary_min, salary_max, is_predicted
        posting.get("description"),
        posting.get("department"),
        posting.get("url"),
        None, None,                            # latitude, longitude
        state,
        json.dumps([]),                        # location_area
        meta.get("company"),                   # search_term: the target company
        meta.get("ingested_at"),
        seen_at,
    )


def flatten(posting: dict, meta: dict, seen_at: str) -> tuple:
    """Map one Adzuna posting onto the raw_postings columns."""
    return (
        meta.get("source", "adzuna"),
        str(posting.get("id")),
        meta.get("country", "us"),
        (posting.get("company") or {}).get("display_name"),
        posting.get("title"),
        (posting.get("location") or {}).get("display_name"),
        posting.get("created"),
        _num(posting.get("salary_min")),
        _num(posting.get("salary_max")),
        _bool(posting.get("salary_is_predicted")),
        posting.get("description"),
        (posting.get("category") or {}).get("label"),
        posting.get("redirect_url"),
        _num(posting.get("latitude")),
        _num(posting.get("longitude")),
        _state(posting),
        json.dumps((posting.get("location") or {}).get("area") or []),
        meta.get("search_term"),
        meta.get("ingested_at"),
        seen_at,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load raw S3 postings into Postgres")
    parser.add_argument("--date", help="ingest_date partition to load (default: today UTC)")
    parser.add_argument("--source", default="adzuna", help="source partition (default adzuna)")
    parser.add_argument("--country", nargs="+", default=["us"], help="country partitions to load")
    parser.add_argument("--boards", action="store_true",
                        help="load company career-board partitions instead of Adzuna")
    parser.add_argument("--dry-run", action="store_true", help="parse but do not write")
    args = parser.parse_args()

    ingest_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen_at = datetime.now(timezone.utc).isoformat()
    bucket = os.getenv("S3_BUCKET")
    if args.boards:
        prefixes = [f"raw/source=company_board/ingest_date={ingest_date}/"]
    else:
        prefixes = [f"raw/source={args.source}/country={c}/ingest_date={ingest_date}/"
                    for c in args.country]

    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    keys: list[str] = []
    for prefix in prefixes:
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            page = s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

    if not keys:
        raise SystemExit(f"No files under {prefixes} - has ingestion run for {ingest_date}?")

    log.info("Loading %s files across %s country partition(s)", len(keys), len(prefixes))

    rows: list[tuple] = []
    for key in keys:
        document = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        meta = document.get("_ingestion_metadata", {})
        mapper = flatten_board if meta.get("source") == "company_board" else flatten
        for posting in document.get("results", []):
            rows.append(mapper(posting, meta, seen_at))

    # The same job can surface under several search terms. Postgres rejects an
    # upsert that touches the same key twice in one statement, so collapse
    # duplicates here, keeping the last occurrence.
    deduped = {(r[0], r[1]): r for r in rows}
    log.info("Parsed %s postings -> %s unique", len(rows), len(deduped))

    if args.dry_run:
        log.info("DRY RUN - nothing written")
        return

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"), port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"), user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw_postings")
            before = cur.fetchone()[0]
            execute_values(cur, UPSERT, list(deduped.values()), page_size=200)
            cur.execute("SELECT count(*) FROM raw_postings")
            after = cur.fetchone()[0]
    finally:
        conn.close()

    log.info("Done. %s new, %s updated, %s total.", after - before,
             len(deduped) - (after - before), after)


if __name__ == "__main__":
    main()
