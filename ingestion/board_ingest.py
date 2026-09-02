"""
Ingest postings from every board in the registry.

This is the job that replaces Adzuna as the source of the job list. Adzuna
supplies 90% of the corpus and every one of its links is a country-gated
adzuna.com redirect, so those postings can never be clicked from outside the
posting's own country. A board posting carries the employer's own URL by
construction, which is the entire point.

Descriptions are the second reason. Adzuna truncates at 500 characters and
the visa boilerplate sits at the END of a posting, which is why sponsorship
detection has been mostly "unclear". Boards return the whole thing - measured
between 1,227 and 8,377 characters.

Workday and SmartRecruiters charge one extra request per posting for that
text, so it is fetched only for postings whose title looks relevant to this
profile. Paying it for all 18,501 registry postings would be tens of
thousands of requests to read text that nothing downstream would ever score.

Usage:
    python ingestion/board_ingest.py                    # every resolved board
    python ingestion/board_ingest.py --company "Capital One"
    python ingestion/board_ingest.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import boto3
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "storage"))
from ats import ADAPTERS  # noqa: E402
from ats.base import clean, get_json  # noqa: E402
from company_boards import in_scope  # noqa: E402
from db import connect  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("board_ingest")

# Titles worth paying a per-posting request to read in full. Deliberately
# broad: a false positive costs one request, a false negative loses a posting
# from the scored set entirely.
RELEVANT_RE = re.compile(
    # Technical roles...
    r"data|analyt|engineer|develop|software|machine learning|\bml\b|\bai\b|scien|"
    r"platform|infrastructure|backend|back-end|python|sql|cloud|devops|sre|security|"
    # ...and anything student-shaped, whatever the discipline. This site is
    # used to find internships, so a finance or audit co-op is as wanted as a
    # data one.
    r"intern|co-?op|new grad|university|student|apprentice|campus|"
    # Canadian employers often name the term instead of the word. RBC posts
    # "2027 CAE, Winter Audit Planning & Reporting Analyst (4 months)" - a
    # Winter 2027 co-op with neither "intern" nor "co-op" in the title, which
    # is why only 25 of their 114 student postings were being kept.
    r"20[0-9][0-9]\s*(winter|summer|fall|spring)|"
    r"(winter|summer|fall|spring)\s*20[0-9][0-9]|"
    r"\([0-9]{1,2}\s*months?\)|[0-9]{1,2}\s*month\s*(term|placement)",
    re.I)

NEEDS_DETAIL = {"workday", "smartrecruiters", "oraclecloud"}


def resolved_boards(conn, only: list[str] | None) -> list[dict]:
    with conn.cursor() as cur:
        if only:
            cur.execute("""SELECT company_name, ats, coords FROM board_registry
                           WHERE status='resolved' AND company_name = ANY(%s)""", (only,))
        else:
            cur.execute("""SELECT company_name, ats, coords, postings_seen
                           FROM board_registry WHERE status='resolved'
                           ORDER BY postings_seen DESC NULLS LAST""")
        return cur.fetchall()


def fetch_board(entry: dict) -> list[dict]:
    """
    All in-scope postings for one company, with descriptions where they matter.

    Order matters here, and getting it wrong silently loses postings. Workday's
    list view collapses a multi-site role to "6 Locations", which no location
    parser can place, so filtering to US/CA before resolving detail discarded
    every multi-location role - 1,852 Capital One postings came out as 518.
    Detail is therefore resolved first and the country filter applied after.

    For the detail-charging boards this also narrows to relevant titles, since
    each one costs a request. That means a Workday board contributes its
    engineering, data and early-career roles rather than its entire
    requisition list - deliberate, and the reason "Part Time Branch
    Ambassador" is not in the corpus.
    """
    ats, coords = entry["ats"], dict(entry["coords"])
    adapter = ADAPTERS[ats]
    jobs = adapter.fetch(**coords)

    if ats not in NEEDS_DETAIL:
        return [j for j in jobs if in_scope(j, {"us", "ca"})]

    wanted = [j for j in jobs if RELEVANT_RE.search(j.get("title") or "")]
    by_id = {d["job_id"]: d for d in _detail_only(ats, coords, wanted)}
    for j in wanted:
        d = by_id.get(j["job_id"])
        if d:
            j["description"] = d.get("description") or j.get("description") or ""
            j["location"] = d.get("location") or j.get("location")
    return [j for j in wanted if in_scope(j, {"us", "ca"})]


def _workday_detail(coords: dict, j: dict) -> dict | None:
    path = "/job/" + j["url"].split("/job/", 1)[1] if "/job/" in (j["url"] or "") else None
    if not path:
        return None
    d = get_json(f"https://{coords['tenant']}.{coords['host']}.myworkdayjobs.com"
                 f"/wday/cxs/{coords['tenant']}/{coords['site']}{path}")
    info = (d or {}).get("jobPostingInfo") or {}
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    return {"job_id": j["job_id"],
            "description": clean(info.get("jobDescription")),
            "location": "; ".join(x for x in locs if x) or None}


def _smartrecruiters_detail(coords: dict, j: dict) -> dict | None:
    d = get_json("https://api.smartrecruiters.com/v1/companies/"
                 f"{coords['slug']}/postings/{j['job_id']}")
    sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
    return {"job_id": j["job_id"], "location": None,
            "description": clean(" ".join(
                (sections.get(k) or {}).get("text", "")
                for k in ("companyDescription", "jobDescription", "qualifications")))}


def _oraclecloud_detail(coords: dict, j: dict) -> dict | None:
    d = get_json("https://{host}/hcmRestApi/resources/latest/"
                 "recruitingCEJobRequisitionDetails".format(host=coords["host"]),
                 params={"expand": "all", "onlyData": "true",
                         "finder": f"ById;Id={j['job_id']},"
                                   f"siteNumber={coords.get('site', 'CX_1')}"})
    item = ((d or {}).get("items") or [{}])[0]
    return {"job_id": j["job_id"],
            "description": clean(item.get("ExternalDescriptionStr")),
            "location": item.get("PrimaryLocation")}


DETAIL_FN = {"workday": _workday_detail,
             "smartrecruiters": _smartrecruiters_detail,
             "oraclecloud": _oraclecloud_detail}


def _detail_only(ats: str, coords: dict, wanted: list[dict],
                 workers: int = 8) -> list[dict]:
    """
    Fetch full text for a chosen subset, concurrently.

    One request per posting is unavoidable on these boards, so the only lever
    is doing them at once. Serially this took over twenty minutes for a single
    Capital One board, which would have made a full sweep of the registry
    impractical.
    """
    fn = DETAIL_FN.get(ats)
    if not fn or not wanted:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda j: fn(coords, j), wanted))
    return [r for r in results if r]


def target_names(args) -> list[str]:
    """Company names from --company/--companies and/or --companies-file."""
    names = list(getattr(args, "company", None) or [])
    if getattr(args, "companies_file", None):
        with open(args.companies_file) as fh:
            names += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    return names


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest every registered career board")
    ap.add_argument("--company", "--companies", nargs="+", dest="company",
                    help="restrict to these companies")
    ap.add_argument("--companies-file", help="file with one company name per line")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = connect(cursor_factory=RealDictCursor)
    try:
        boards = resolved_boards(conn, target_names(args) or None)
    finally:
        conn.close()
    log.info("%d resolved boards", len(boards))

    bucket = os.getenv("S3_BUCKET")
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    ingested_at = datetime.now(UTC).isoformat()
    total = 0

    for e in boards:
        name = e["company_name"]
        try:
            kept = fetch_board(e)
        except Exception as exc:
            log.warning("  %-28s FAILED %s: %s", name, type(exc).__name__, str(exc)[:70])
            continue
        withtext = sum(1 for j in kept if len(j.get("description") or "") > 500)
        log.info("  %-28s %-16s %5d in scope, %4d with full text",
                 name, e["ats"], len(kept), withtext)
        total += len(kept)

        if kept and not args.dry_run:
            doc = {"_ingestion_metadata": {
                       "source": "company_board", "board": e["ats"], "company": name,
                       "ingested_at": ingested_at, "result_count": len(kept)},
                   "results": kept}
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            s3.put_object(
                Bucket=bucket,
                Key=f"raw/source=company_board/ingest_date={today}/{slug}.json",
                Body=json.dumps(doc, indent=2).encode(), ContentType="application/json")

    log.info("%s postings across %d boards", f"{total:,}", len(boards))


if __name__ == "__main__":
    main()
