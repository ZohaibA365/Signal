"""
Daily market snapshot - the compounding data asset.

For each tracked technology this records how many US openings mention it,
which employers hire most for it, and how salaries are distributed. Run daily,
those rows become a time series of technology demand.

Why it has to be accumulated rather than queried: Adzuna's `history` endpoint
only covers recognised job categories. Verified against the live API - it
returns 12 months of data for "data engineer" and nothing for "snowflake".
No public source publishes per-technology demand over time, so the only way
to have it is to start recording and keep recording.

That is also what makes the project hard to copy. Someone who clones this repo
gets the code. They do not get the history.

Usage:
    python ingestion/market_snapshot.py              # full capture
    python ingestion/market_snapshot.py --counts-only  # faster, counts only
    python ingestion/market_snapshot.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import boto3
import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_layer"))
from taxonomy import Tech, tracked  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("market_snapshot")

BASE = "https://api.adzuna.com/v1/api/jobs/us"
SLEEP = 1.0          # no rate-limit headers are exposed; stay conservative
MAX_RETRIES = 3
TOP_COMPANIES_KEPT = 15


def _auth() -> dict:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_API_KEY")
    if not (app_id and app_key):
        raise SystemExit("ADZUNA_APP_ID / ADZUNA_API_KEY missing from .env")
    return {"app_id": app_id, "app_key": app_key}


def _get(path: str, params: dict) -> dict | None:
    """GET with the same retry policy as the posting ingester."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(f"{BASE}/{path}", params=params, timeout=30)
        except requests.RequestException as exc:
            log.warning("    network error (%s), retry %s/%s", exc, attempt, MAX_RETRIES)
            time.sleep(2**attempt)
            continue

        if r.status_code == 200:
            return r.json()
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2**attempt)
            continue
        log.warning("    HTTP %s on %s - skipping", r.status_code, path)
        return None
    return None


def capture(tech: Tech, auth: dict, counts_only: bool) -> dict:
    """Collect every metric for one technology."""
    q = tech.search_query
    out: dict = {"tech": tech.slug, "name": tech.name, "category": tech.category,
                 "query": q, "openings": None, "companies": [], "salary": {}}

    search = _get("search/1", {**auth, "what": q, "results_per_page": 1})
    if search is not None:
        out["openings"] = search.get("count", 0)
    time.sleep(SLEEP)

    if counts_only:
        return out

    top = _get("top_companies", {**auth, "what": q})
    if top:
        out["companies"] = [
            {"name": c.get("canonical_name") or c.get("display_name"),
             "postings": c.get("count", 0)}
            for c in (top.get("leaderboard") or [])[:TOP_COMPANIES_KEPT]
            if (c.get("canonical_name") or c.get("display_name"))
        ]
    time.sleep(SLEEP)

    hist = _get("histogram", {**auth, "what": q})
    if hist:
        out["salary"] = hist.get("histogram") or {}
    time.sleep(SLEEP)

    return out


UPSERT_SNAP = """
INSERT INTO market_snapshots
    (snapshot_date, tech_slug, tech_name, category, search_query, openings)
VALUES %s
ON CONFLICT (snapshot_date, tech_slug) DO UPDATE SET
    openings = EXCLUDED.openings, captured_at = NOW()
"""

UPSERT_CO = """
INSERT INTO market_snapshot_companies
    (snapshot_date, tech_slug, company_name, postings, rank)
VALUES %s
ON CONFLICT (snapshot_date, tech_slug, company_name) DO UPDATE SET
    postings = EXCLUDED.postings, rank = EXCLUDED.rank
"""

UPSERT_SAL = """
INSERT INTO market_snapshot_salary
    (snapshot_date, tech_slug, salary_bucket, posting_count)
VALUES %s
ON CONFLICT (snapshot_date, tech_slug, salary_bucket) DO UPDATE SET
    posting_count = EXCLUDED.posting_count
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture the daily market snapshot")
    ap.add_argument("--counts-only", action="store_true",
                    help="openings only; skips top_companies and histogram")
    ap.add_argument("--dry-run", action="store_true", help="fetch but do not persist")
    args = ap.parse_args()

    auth = _auth()
    techs = tracked()
    today = datetime.now(timezone.utc).date()
    calls = len(techs) * (1 if args.counts_only else 3)
    log.info("Snapshot %s: %s technologies, ~%s API calls, ~%.0fs",
             today, len(techs), calls, calls * SLEEP)

    results = [capture(t, auth, args.counts_only) for t in
               (log.info("  %-22s ...", t.name) or t for t in techs)]

    ok = [r for r in results if r["openings"] is not None]
    log.info("Captured %s/%s technologies", len(ok), len(techs))

    # Land the raw response in S3 first, so the warehouse can always be rebuilt.
    if not args.dry_run and os.getenv("S3_BUCKET"):
        boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1")).put_object(
            Bucket=os.getenv("S3_BUCKET"),
            Key=f"market/source=adzuna/snapshot_date={today}/snapshot.json",
            Body=json.dumps({"snapshot_date": str(today), "results": results},
                            indent=2).encode(),
            ContentType="application/json",
        )
        log.info("Raw snapshot written to s3://%s/market/snapshot_date=%s/",
                 os.getenv("S3_BUCKET"), today)

    for r in sorted(ok, key=lambda x: -(x["openings"] or 0))[:10]:
        log.info("  %-22s %8s openings", r["name"], f"{r['openings']:,}")

    if args.dry_run:
        log.info("DRY RUN - nothing persisted")
        return

    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"), port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"), user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, UPSERT_SNAP, [
                (today, r["tech"], r["name"], r["category"], r["query"], r["openings"])
                for r in ok
            ])

            companies = [
                (today, r["tech"], c["name"], c["postings"], i)
                for r in ok for i, c in enumerate(r["companies"], 1)
            ]
            if companies:
                execute_values(cur, UPSERT_CO, companies)

            # Adzuna returns the histogram as {"140000": 9003, ...}. Non-numeric
            # keys have been seen occasionally, so parse defensively.
            salary = []
            for r in ok:
                for bucket, count in (r["salary"] or {}).items():
                    try:
                        salary.append((today, r["tech"], int(bucket), int(count)))
                    except (TypeError, ValueError):
                        continue
            if salary:
                execute_values(cur, UPSERT_SAL, salary)

            log.info("Persisted %s snapshots, %s company rows, %s salary buckets",
                     len(ok), len(companies), len(salary))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
