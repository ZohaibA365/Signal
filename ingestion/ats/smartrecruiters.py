"""
SmartRecruiters. Common among large non-tech employers (retail, industrial).

The list endpoint omits the description, so full text costs one request per
posting - same trade-off as Workday, and off by default for the same reason.
"""
from __future__ import annotations

import time

from .base import SLEEP, clean, get_json

NAME = "smartrecruiters"
API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
PAGE = 100


def probe(slug: str) -> int | None:
    d = get_json(API.format(slug=slug), params={"limit": 10})
    # The API answers 200 with totalFound 0 for a slug that does not exist,
    # so a zero total means "wrong slug", not "no openings".
    return d.get("totalFound") or None if isinstance(d, dict) else None


def _loc(j: dict) -> str | None:
    loc = j.get("location") or {}
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    return ", ".join(p for p in parts if p) or None


def fetch(slug: str, limit: int | None = None,
          with_descriptions: bool = False, **_) -> list[dict]:
    out, offset = [], 0
    while True:
        d = get_json(API.format(slug=slug), params={"limit": PAGE, "offset": offset})
        if not isinstance(d, dict):
            break
        batch = d.get("content") or []
        if not batch:
            break
        for j in batch:
            out.append({
                "board": NAME,
                "job_id": str(j.get("id")),
                "title": j.get("name"),
                "location": _loc(j),
                "description": "",
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "posted": j.get("releasedDate"),
                "department": (j.get("department") or {}).get("label"),
            })
        offset += PAGE
        if (limit and len(out) >= limit) or offset >= (d.get("totalFound") or 0):
            break
        time.sleep(SLEEP)

    if limit:
        out = out[:limit]
    if with_descriptions:
        for p in out:
            d = get_json(f"{API.format(slug=slug)}/{p['job_id']}")
            sections = ((d or {}).get("jobAd") or {}).get("sections") or {}
            p["description"] = clean(" ".join(
                (sections.get(k) or {}).get("text", "")
                for k in ("companyDescription", "jobDescription", "qualifications")))
            time.sleep(SLEEP)
    return out
