"""Ashby."""
from __future__ import annotations

from .base import clean, get_json

NAME = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def probe(slug: str) -> int | None:
    d = get_json(API.format(slug=slug), params={"includeCompensation": "true"})
    return len(d.get("jobs") or []) if isinstance(d, dict) and d.get("jobs") else None


def fetch(slug: str, **_) -> list[dict]:
    d = get_json(API.format(slug=slug), params={"includeCompensation": "true"})
    return [{
        "board": NAME,
        "job_id": str(j.get("id")),
        "title": j.get("title"),
        "location": j.get("location"),
        "description": clean(j.get("descriptionPlain") or j.get("descriptionHtml")),
        "url": j.get("jobUrl"),
        "posted": j.get("publishedAt"),
        "department": j.get("department"),
    } for j in (d.get("jobs") or [])] if isinstance(d, dict) else []
