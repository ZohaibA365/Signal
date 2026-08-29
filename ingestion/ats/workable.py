"""Workable. Mid-market; its widget endpoint returns full descriptions inline."""
from __future__ import annotations

from .base import clean, get_json

NAME = "workable"
API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


def probe(slug: str) -> int | None:
    d = get_json(API.format(slug=slug), params={"details": "true"})
    return len(d.get("jobs") or []) if isinstance(d, dict) and d.get("jobs") else None


def _loc(j: dict) -> str | None:
    loc = j.get("location") or {}
    if isinstance(loc, str):
        return loc
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    return ", ".join(p for p in parts if p) or None


def fetch(slug: str, **_) -> list[dict]:
    d = get_json(API.format(slug=slug), params={"details": "true"})
    return [{
        "board": NAME,
        "job_id": str(j.get("shortcode") or j.get("id")),
        "title": j.get("title"),
        "location": _loc(j),
        "description": clean(j.get("description")),
        "url": j.get("url") or f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
        "posted": j.get("published_on") or j.get("created_at"),
        "department": j.get("department"),
    } for j in (d.get("jobs") or [])] if isinstance(d, dict) else []
