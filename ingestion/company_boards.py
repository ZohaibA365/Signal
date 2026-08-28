"""
Company career-board ingestion: Greenhouse, Lever, Ashby.

Why this exists alongside the Adzuna ingester:

  1. Aggregators barely index startups. Measured against the live APIs -
     Stripe 580 postings here vs 3 in Adzuna, Databricks 845 vs 7, Figma 160
     vs 3. Adzuna has no usable employer filter either: its `company`
     parameter returns HTTP 400, and putting the name in the query is
     unreliable ("Ramp data engineer" returned three results, none at Ramp).
     So a hand-picked target list can only be served from the boards.

  2. Boards return the FULL description. Adzuna truncates at 500 characters,
     which is why sponsorship detection has been mostly "unclear" - the legal
     boilerplate that states it sits at the end of a posting and was always
     being cut off. A Databricks posting here carries 5,267 characters.

Not every company resolves. Ramp and Notion appear on neither Greenhouse nor
Lever under any obvious slug, so unresolved names are reported rather than
silently dropped - a target list that quietly loses a third of its entries is
worse than one that says which ones failed.

Usage:
    python ingestion/company_boards.py --companies Stripe Databricks Figma
    python ingestion/company_boards.py --companies-file targets.txt --country us
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import boto3
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("company_boards")

TIMEOUT = 20
SLEEP = 0.3

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER = "https://api.lever.co/v0/postings/{slug}"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Location filtering. Boards are global - the first Databricks posting
# returned is a Tokyo role - and each board writes locations differently:
#   Greenhouse  "San Francisco, CA"        (abbreviation)
#   Ashby       "San Francisco, California" (full state name)
#   Ashby       "US-CA-Menlo Park"          (ISO country prefix)
# A single regex over abbreviations missed the latter two entirely, scoring
# Notion 0-of-135 and Snowflake 1-of-385. Explicit sets are clearer here.

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia", "washington dc",
}
CA_PROVINCES = {
    "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "prince edward island",
}
STATE_ABBR = re.compile(
    r",\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    r"MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|"
    r"WI|WY|DC|ON|QC|BC|AB|MB|SK|NS|NB|NL|PE)\b"
)
ISO_PREFIX = re.compile(r"^(US|CA)[-/]", re.IGNORECASE)
COUNTRY_WORDS = re.compile(r"(united states|\bu\.?s\.?a?\b|canada)", re.IGNORECASE)


def _looks_us_ca(loc: str) -> bool:
    """True when a board location string denotes a US or Canadian worksite."""
    low = loc.lower()
    if ISO_PREFIX.match(loc.strip()):
        return True
    if COUNTRY_WORDS.search(low):
        return True
    if STATE_ABBR.search(loc):
        return True
    return any(name in low for name in US_STATES | CA_PROVINCES)


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")


def slug_variants(name: str) -> list[str]:
    """
    Candidate board slugs for a company name.

    Boards use an opaque slug that usually - not always - derives from the
    company name. Trying a few cheap variants resolves most targets without
    needing a lookup table.
    """
    base = name.strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    stripped = re.sub(r"\b(inc|llc|ltd|corp|corporation|company|technologies|labs)\b",
                      "", base).strip()
    stripped_compact = re.sub(r"[^a-z0-9]", "", stripped)
    seen, out = set(), []
    for v in (compact, hyphen, stripped_compact):
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _clean(html: str | None) -> str:
    """Board descriptions are HTML; the warehouse stores plain text."""
    if not html:
        return ""
    text = html.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = TAG_RE.sub(" ", text)
    return WS_RE.sub(" ", text).strip()


def _try_greenhouse(slug: str) -> list[dict] | None:
    r = requests.get(GREENHOUSE.format(slug=slug), params={"content": "true"}, timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    jobs = r.json().get("jobs") or []
    return [{
        "board": "greenhouse",
        "job_id": str(j.get("id")),
        "title": j.get("title"),
        "location": (j.get("location") or {}).get("name"),
        "description": _clean(j.get("content")),
        "url": j.get("absolute_url"),
        "posted": j.get("first_published") or j.get("updated_at"),
        "department": ", ".join(d.get("name", "") for d in (j.get("departments") or [])),
    } for j in jobs] or None


def _try_lever(slug: str) -> list[dict] | None:
    r = requests.get(LEVER.format(slug=slug), params={"mode": "json"}, timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    jobs = r.json()
    if not isinstance(jobs, list) or not jobs:
        return None
    return [{
        "board": "lever",
        "job_id": str(j.get("id")),
        "title": j.get("text"),
        "location": (j.get("categories") or {}).get("location"),
        "description": _clean(j.get("descriptionPlain") or j.get("description")),
        "url": j.get("hostedUrl"),
        "posted": (datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).isoformat()
                   if j.get("createdAt") else None),
        "department": (j.get("categories") or {}).get("team"),
    } for j in jobs]


def _try_ashby(slug: str) -> list[dict] | None:
    r = requests.get(ASHBY.format(slug=slug), params={"includeCompensation": "true"},
                     timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    jobs = r.json().get("jobs") or []
    if not jobs:
        return None
    return [{
        "board": "ashby",
        "job_id": str(j.get("id")),
        "title": j.get("title"),
        "location": j.get("location"),
        "description": _clean(j.get("descriptionPlain") or j.get("descriptionHtml")),
        "url": j.get("jobUrl"),
        "posted": j.get("publishedAt"),
        "department": j.get("department"),
    } for j in jobs]


def fetch_company(name: str) -> tuple[str | None, list[dict]]:
    """Try each board and slug variant. Returns (board_used, postings)."""
    for slug in slug_variants(name):
        for fn in (_try_greenhouse, _try_lever, _try_ashby):
            try:
                jobs = fn(slug)
            except (requests.RequestException, ValueError):
                jobs = None
            time.sleep(SLEEP)
            if jobs:
                return f"{jobs[0]['board']}:{slug}", jobs
    return None, []


def in_scope(posting: dict, countries: set[str]) -> bool:
    """Boards are global; keep only postings in the requested markets."""
    if not countries:
        return True
    loc = (posting.get("location") or "").strip()
    if not loc:
        return False
    # A bare "Remote" with no country could be anywhere, so it is excluded;
    # "Remote - US" is kept by the country check below.
    return _looks_us_ca(loc)


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest company career boards")
    ap.add_argument("--companies", nargs="+", help="company names")
    ap.add_argument("--companies-file", help="file with one company name per line")
    ap.add_argument("--country", nargs="+", default=["us", "ca"],
                    help="restrict to these markets; pass 'all' for no filter")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    names = list(args.companies or [])
    if args.companies_file:
        with open(args.companies_file) as fh:
            names += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not names:
        raise SystemExit("Give --companies or --companies-file")

    countries = set() if "all" in args.country else set(args.country)
    bucket = os.getenv("S3_BUCKET")
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ingested_at = datetime.now(timezone.utc).isoformat()

    resolved, unresolved, total_kept = [], [], 0

    for name in names:
        board, jobs = fetch_company(name)
        if not board:
            unresolved.append(name)
            log.warning("  %-24s no board found (tried %s)", name, ", ".join(slug_variants(name)))
            continue

        kept = [j for j in jobs if in_scope(j, countries)]
        resolved.append((name, board, len(jobs), len(kept)))
        total_kept += len(kept)
        log.info("  %-24s %-22s %4d postings, %4d in scope", name, board, len(jobs), len(kept))

        if kept and not args.dry_run:
            doc = {
                "_ingestion_metadata": {
                    "source": "company_board",
                    "board": board,
                    "company": name,
                    "ingested_at": ingested_at,
                    "result_count": len(kept),
                    "total_on_board": len(jobs),
                },
                "results": kept,
            }
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            s3.put_object(
                Bucket=bucket,
                Key=f"raw/source=company_board/ingest_date={today}/{slug}.json",
                Body=json.dumps(doc, indent=2).encode(),
                ContentType="application/json",
            )

    log.info("")
    log.info("Resolved %s/%s companies, %s postings in scope.",
             len(resolved), len(names), f"{total_kept:,}")
    if unresolved:
        # Reported deliberately: a target list that silently loses entries is
        # worse than one that names its failures.
        log.warning("Unresolved (need a manual slug or another board): %s",
                    ", ".join(unresolved))


if __name__ == "__main__":
    main()
