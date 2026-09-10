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
from datetime import UTC, datetime

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
    # Canadian student hiring is called co-op, not internship. Without these
    # the Canadian feed returns almost no student roles: the corpus held 267
    # Canadian postings and 2 intern-shaped titles, against 1,749 in the US,
    # because the Canadian ingest had only ever been run with the mainframe
    # profile's search terms.
    "data engineer co-op",
    "software engineer co-op",
    "engineering co-op student",
    "data analyst co-op",
]

# Be polite to a free-tier API.
SLEEP_BETWEEN_CALLS = 1.0
MAX_RETRIES = 4

# Statuses worth trying again. 403 is here because Adzuna publishes no rate
# limits and a throttle mid-run looks identical to a bad key from one response.
RETRYABLE = {403, 429}


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name} (check your .env)")
    return value


class FetchFailed(Exception):
    """One page could not be fetched. Carries the status so callers can report it."""

    def __init__(self, status: int | None, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def fetch_page(term: str, page: int, app_id: str, app_key: str,
               country: str = DEFAULT_COUNTRY) -> dict:
    """
    Fetch one page, retrying what is worth retrying.

    403 is in the retryable set deliberately. Adzuna publishes no rate limits
    and the daily volume here is around 300 calls - 51 technologies x 3 for the
    market snapshot, plus up to 150 for this ingest - so a refusal partway
    through a run is far more likely to be a throttle or a WAF blip than a
    genuinely wrong key. A wrong key fails on the first call, and the caller
    can tell the difference because nothing at all will have succeeded.

    Raises FetchFailed rather than RuntimeError, so the caller can record the
    failure and carry on instead of losing the rest of the run.
    """
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": term,
        "results_per_page": RESULTS_PER_PAGE,
        "content-type": "application/json",
    }

    last = "no attempt made"
    status = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                API_URL.format(country=country, page=page), params=params, timeout=30
            )
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            log.warning("  %s on '%s' page %s - retry %s/%s", last, term, page,
                        attempt, MAX_RETRIES)
            time.sleep(2**attempt)
            continue

        status = response.status_code
        if status == 200:
            return response.json()

        # 429 rate limited, 403 throttled or quota, 5xx Adzuna's problem.
        if status in RETRYABLE or status >= 500:
            # Honour Retry-After when the server bothers to send one; it knows
            # better than an exponential guess.
            backoff = 2**attempt
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                backoff = min(int(retry_after), 60)
            last = f"HTTP {status}: {response.text[:200]}"
            log.warning(
                "  HTTP %s on '%s' page %s - retry %s/%s in %ss",
                status, term, page, attempt, MAX_RETRIES, backoff,
            )
            time.sleep(backoff)
            continue

        raise FetchFailed(status, f"HTTP {status} for '{term}' page {page}: "
                                  f"{response.text[:200]}")

    raise FetchFailed(status, f"gave up after {MAX_RETRIES} retries for "
                              f"'{term}' page {page} - {last}")


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


def _annotate(level: str, message: str) -> None:
    """A GitHub annotation, which is the only failure channel the public API returns."""
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::{level}::{message.replace(chr(10), ' ')}")


def report(total_postings: int, files_written: int, skipped: int, planned: int,
           failures: list, args) -> None:
    """
    Say what the run actually did, and decide whether it failed.

    Two things this fixes. First, a partial ingest is now reported rather than
    thrown away: the process exits non-zero only when it achieved nothing at
    all, because fourteen terms succeeding and one failing is a warning, not a
    dead run.

    Second, it stops --resume hiding a dead ingest. Three consecutive runs
    "succeeded" at this step in 6, 7 and 9 seconds because every page was
    already present for that date and the loop skipped all of them. Nothing in
    the log distinguished that from genuinely ingesting 150 pages, so when the
    next fresh date arrived and the ingest ran for real, its first actual
    failure in days looked like a sudden regression.
    """
    log.info("Done. %s postings across %s files.", f"{total_postings:,}", files_written)
    log.info("  planned %s pages | written %s | resumed %s | failed %s",
             planned, files_written, skipped, len(failures))

    if skipped and not files_written and not failures:
        log.warning("  nothing to do: every one of %s pages was already present "
                    "for this date", skipped)

    if failures:
        by_status: dict = {}
        for _country, _term, _page, status, _detail in failures:
            by_status[status] = by_status.get(status, 0) + 1
        summary = ", ".join(f"HTTP {k}: {v}" for k, v in sorted(
            by_status.items(), key=lambda kv: (kv[0] is None, kv[0])))
        log.error("  %s term(s) failed (%s)", len(failures), summary)
        for country, term, page, _status, detail in failures[:10]:
            log.error("    %s/%s page %s -> %s", country, term, page, detail)

    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(f"\n**Aggregator ingest** - {total_postings:,} postings, "
                     f"{files_written} written, {skipped} already present, "
                     f"{len(failures)} failed of {planned} planned.\n")
            for country, term, page, _status, detail in failures[:10]:
                fh.write(f"- `{country}` / `{term}` page {page}: {detail}\n")

    achieved_nothing = total_postings == 0 and skipped == 0

    if failures and achieved_nothing:
        # Every call failed and nothing was already present. That is a real
        # outage - a revoked key, an exhausted quota, or Adzuna down - and it
        # should be loud even though the step is continue-on-error.
        _annotate("error", f"Adzuna ingest collected nothing: {len(failures)} "
                           f"failures, first was {failures[0][4]}")
        raise SystemExit(1)

    if failures:
        _annotate("warning", f"Adzuna ingest partially failed: {len(failures)} of "
                             f"{planned} pages. First: {failures[0][4]}")


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
    ingest_date = datetime.now(UTC).strftime("%Y-%m-%d")
    ingested_at = datetime.now(UTC).isoformat()

    log.info("Ingest date %s -> s3://%s%s", ingest_date, bucket, "  (DRY RUN)" if args.dry_run else "")

    total_postings = 0
    files_written = 0
    skipped = 0
    failures: list[tuple[str, str, int, int | None, str]] = []
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

            try:
                payload = fetch_page(term, page, app_id, app_key, country)
            except FetchFailed as exc:
                # Record and move to the next term. Previously this raised and
                # abandoned every remaining term AND the second country,
                # throwing away work already done - on 2026-09-10 that turned
                # one bad call into a skipped board ingest, skipped loads and a
                # permanently missing day of archive history.
                failures.append((country, term, page, exc.status, exc.detail))
                log.error("  %s/%s page %s FAILED: %s", country, term, page, exc.detail)
                break

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

    report(total_postings, files_written, skipped, planned, failures, args)


if __name__ == "__main__":
    main()
