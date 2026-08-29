"""
Amazon's own job API.

Amazon is one of the largest employers in this corpus and runs no third-party
board, so without a bespoke adapter its postings can only come from an
aggregator - which means a link that cannot be clicked. It also hires interns
at a scale that matters for this profile, so it earns the exception.

The endpoint is a search rather than a board dump, so it is walked query by
query instead of page-by-page over everything. Queries are therefore part of
the registry entry, and default to the roles this project tracks.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from .base import SLEEP, clean, get_json

NAME = "amazon"
API = "https://www.amazon.jobs/en/search.json"
PAGE = 100
MAX_PAGES = 20

DEFAULT_QUERIES = (
    "data engineer", "software development engineer intern", "data scientist",
    "business intelligence engineer", "machine learning engineer",
    "software development engineer", "analytics", "data engineer intern",
)
DEFAULT_COUNTRIES = ("USA", "CAN")


def _posted(text: str | None) -> str | None:
    """Amazon prints dates as "August  7, 2026", with the padding included."""
    if not text:
        return None
    try:
        return datetime.strptime(re.sub(r"\s+", " ", text).strip(), "%B %d, %Y") \
                       .date().isoformat()
    except ValueError:
        return None


def probe(**_) -> int | None:
    d = get_json(API, params={"base_query": "data engineer", "result_limit": 1,
                              "country": "USA"})
    return (d or {}).get("hits") or None


def fetch(queries: list[str] | None = None, countries: list[str] | None = None,
          limit: int | None = None, **_) -> list[dict]:
    queries = list(queries or DEFAULT_QUERIES)
    countries = list(countries or DEFAULT_COUNTRIES)
    seen: dict[str, dict] = {}

    for country in countries:
        for q in queries:
            for page in range(MAX_PAGES):
                d = get_json(API, params={"base_query": q, "result_limit": PAGE,
                                          "offset": page * PAGE, "country": country})
                jobs = (d or {}).get("jobs") or []
                if not jobs:
                    break
                for j in jobs:
                    jid = str(j.get("id_icims") or j.get("id"))
                    if jid in seen:
                        continue
                    # basic_qualifications carries the sponsorship and
                    # eligibility language, so it is kept with the description.
                    body = " ".join(filter(None, (
                        j.get("description"), j.get("basic_qualifications"),
                        j.get("preferred_qualifications"))))
                    seen[jid] = {
                        "board": NAME,
                        "job_id": jid,
                        "title": j.get("title"),
                        "location": j.get("normalized_location") or j.get("location"),
                        "description": clean(body),
                        "url": f"https://www.amazon.jobs{j.get('job_path')}",
                        "posted": _posted(j.get("posted_date")),
                        "department": j.get("job_family") or j.get("job_category"),
                    }
                if len(jobs) < PAGE or (limit and len(seen) >= limit):
                    break
                time.sleep(SLEEP)
            if limit and len(seen) >= limit:
                break
    out = list(seen.values())
    return out[:limit] if limit else out
