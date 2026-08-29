"""Lever."""
from __future__ import annotations

from datetime import UTC, datetime

from .base import clean, get_json

NAME = "lever"
API = "https://api.lever.co/v0/postings/{slug}"


def probe(slug: str) -> int | None:
    d = get_json(API.format(slug=slug), params={"mode": "json"})
    return len(d) if isinstance(d, list) and d else None


def fetch(slug: str, **_) -> list[dict]:
    d = get_json(API.format(slug=slug), params={"mode": "json"})
    if not isinstance(d, list):
        return []
    return [{
        "board": NAME,
        "job_id": str(j.get("id")),
        "title": j.get("text"),
        "location": (j.get("categories") or {}).get("location"),
        "description": clean(j.get("descriptionPlain") or j.get("description")),
        "url": j.get("hostedUrl"),
        "posted": (datetime.fromtimestamp(j["createdAt"] / 1000, UTC).isoformat()
                   if j.get("createdAt") else None),
        "department": (j.get("categories") or {}).get("team"),
    } for j in d]
