"""
Adzuna -> S3 ingestion.

Pulls US job postings from the Adzuna API and writes the raw, unmodified JSON
to the S3 data lake, partitioned by ingest date.

Design note: this is the RAW layer, so it deliberately does no filtering,
cleaning, or reshaping. Whatever the API returns is what gets stored. That
means a bad downstream decision (wrong role filter, wrong seniority cutoff)
can always be corrected by reprocessing history, instead of silently losing
data that was never collected in the first place.

Usage:
    python ingestion/adzuna_ingest.py                 # all default search terms
    python ingestion/adzuna_ingest.py --pages 3       # 3 pages per term
    python ingestion/adzuna_ingest.py --dry-run       # fetch but don't write to S3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("adzuna_ingest")

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
# Adzuna exposes one endpoint per country. Salaries are quoted in that
# country's own currency, so US and CA figures must never be compared
# or aggregated without conversion.
DEFAULT_COUNTRY = "us"

# Adzuna caps results_per_page at 50.
RESULTS_PER_PAGE = 50

# Roles Signal tracks. Ordered by relevance to a data-engineering resume.
SEARCH_TERMS = [
    # Broad role coverage.
    "data engineer",
    "analytics engineer",
    "machine learning engineer",
    "ai engineer",
    # Explicitly student-facing. Without these the feed is ~97% mid/senior
    # roles, because a general "data engineer" query rarely returns postings
    # that are titled as internships or new-grad positions.
    "data engineer intern",
    "data science intern",
    "software engineer intern",
    "machine learning intern",
    "data analyst intern",
    "new grad data engineer",
    "entry level data engineer",
]

# Be polite to a free-tier API.
SLEEP_BETWEEN_CALLS = 1.0
MAX_RETRIES = 3


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name} (check your .env)")
    return value


def fetch_page(term: str, page: int, app_id: str, app_key: str,
               country: str = DEFAULT_COUNTRY) -> dict:
    """Fetch one page of results, retrying on rate limits and transient errors."""
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": term,
        "results_per_page": RESULTS_PER_PAGE,
        "content-type": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(
            API_URL.format(country=country, page=page), params=params, timeout=30
        )

        if response.status_code == 200:
            return response.json()

        # 429 = rate limited, 5xx = Adzuna's problem. Both are worth retrying
        # with exponential backoff. Anything else is our bug - fail loudly.
        if response.status_code == 429 or response.status_code >= 500:
            backoff = 2**attempt
            log.warning(
                "  HTTP %s on '%s' page %s - retry %s/%s in %ss",
                response.status_code, term, page, attempt, MAX_RETRIES, backoff,
            )
            time.sleep(backoff)
            continue

        raise RuntimeError(
            f"Adzuna returned HTTP {response.status_code} for '{term}' page {page}: "
            f"{response.text[:300]}"
        )

    raise RuntimeError(f"Adzuna failed after {MAX_RETRIES} retries for '{term}' page {page}")


def s3_key(term: str, page: int, ingest_date: str, country: str = DEFAULT_COUNTRY) -> str:
    """
    Hive-style partitioning: source=.../ingest_date=...

    This layout is what lets AWS Glue, Athena, and Spark discover partitions
    automatically and skip irrelevant files when querying a date range.
    """
    slug = term.replace(" ", "_")
    return f"raw/source=adzuna/country={country}/ingest_date={ingest_date}/{slug}__page{page:03d}.json"


def existing_keys(s3, bucket: str, ingest_date: str, country: str) -> set[str]:
    """
    Every object already present for this date partition.

    A deep backfill is ~1,100 sequential requests. Without this, a failure at
    request 1,000 means starting over and re-spending the whole run.
    """
    prefix = f"raw/source=adzuna/country={country}/ingest_date={ingest_date}/"
    keys: set[str] = set()
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        keys.update(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Adzuna job postings into S3")
    parser.add_argument("--pages", type=int, default=2, help="pages per search term (default 2)")
    parser.add_argument("--terms", nargs="+", default=SEARCH_TERMS, help="override search terms")
    parser.add_argument("--dry-run", action="store_true", help="fetch but do not write to S3")
    parser.add_argument("--country", nargs="+", default=[DEFAULT_COUNTRY],
                        help="Adzuna country codes, e.g. us ca")
    parser.add_argument("--resume", action="store_true",
                        help="skip (term, page) combinations already in S3 for today")
    args = parser.parse_args()

    app_id = _require_env("ADZUNA_APP_ID")
    app_key = _require_env("ADZUNA_API_KEY")
    bucket = _require_env("S3_BUCKET")
    region = os.getenv("AWS_REGION", "us-east-1")

    s3 = boto3.client("s3", region_name=region)
    ingest_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ingested_at = datetime.now(timezone.utc).isoformat()

    log.info("Ingest date %s -> s3://%s%s", ingest_date, bucket, "  (DRY RUN)" if args.dry_run else "")

    total_postings = 0
    files_written = 0
    skipped = 0
    planned = len(args.terms) * args.pages * len(args.country)

    for country in args.country:
      already = (existing_keys(s3, bucket, ingest_date, country)
                 if (args.resume and not args.dry_run) else set())
      if already:
          log.info("Resume: %s objects already present for %s/%s",
                   len(already), country, ingest_date)
      log.info("Country: %s", country.upper())

      for term in args.terms:
        for page in range(1, args.pages + 1):
            if s3_key(term, page, ingest_date, country) in already:
                skipped += 1
                continue

            done = files_written + skipped
            if done and done % 25 == 0:
                log.info("  progress %s/%s pages, %s postings so far",
                         done, planned, f"{total_postings:,}")

            payload = fetch_page(term, page, app_id, app_key, country)
            results = payload.get("results", [])

            if not results:
                log.info("  '%s' page %s - no results, moving on", term, page)
                break

            # Wrap the untouched API response with provenance metadata, so
            # downstream layers know exactly where each record came from and when.
            document = {
                "_ingestion_metadata": {
                    "source": "adzuna",
                    "country": country,
                    "search_term": term,
                    "page": page,
                    "ingested_at": ingested_at,
                    "result_count": len(results),
                    "total_available": payload.get("count"),
                },
                "results": results,
            }

            key = s3_key(term, page, ingest_date, country)
            if args.dry_run:
                log.info("  would write %s postings -> %s", len(results), key)
            else:
                try:
                    s3.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=json.dumps(document, indent=2).encode("utf-8"),
                        ContentType="application/json",
                    )
                except ClientError as exc:
                    raise SystemExit(f"Failed writing to s3://{bucket}/{key}: {exc}") from exc
                log.info("  wrote %s postings -> %s", len(results), key)
                files_written += 1

            total_postings += len(results)
            time.sleep(SLEEP_BETWEEN_CALLS)

    log.info("Done. %s postings across %s files (%s skipped as already present).",
             f"{total_postings:,}", files_written, skipped)


if __name__ == "__main__":
    main()
