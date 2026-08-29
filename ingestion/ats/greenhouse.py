"""Greenhouse. Startup-heavy, and the only board that returns full text for free."""
from __future__ import annotations

from .base import clean, get_json

NAME = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def probe(slug: str) -> int | None:
    d = get_json(API.format(slug=slug), params={"content": "false"})
    return len(d.get("jobs") or []) if isinstance(d, dict) and d.get("jobs") else None


def fetch(slug: str, **_) -> list[dict]:
    d = get_json(API.format(slug=slug), params={"content": "true"})
    return [{
        "board": NAME,
        "job_id": str(j.get("id")),
        "title": j.get("title"),
        "location": (j.get("location") or {}).get("name"),
        "description": clean(j.get("content")),
        "url": j.get("absolute_url"),
        "posted": j.get("first_published") or j.get("updated_at"),
        "department": ", ".join(d.get("name", "") for d in (j.get("departments") or [])),
    } for j in (d.get("jobs") or [])] if isinstance(d, dict) else []
