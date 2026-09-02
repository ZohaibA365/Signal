"""
Rippling's applicant tracking board.

Worth an adapter because Rippling skews to newer startups and scale-ups -
the segment that runs co-op and internship programmes and that aggregators
index poorly. Descriptions are full (13,000 characters on the sample checked)
and the apply URL is the employer's own posting on ats.rippling.com.

Coordinates are a single slug, so this board can be swept blind alongside
Greenhouse, Lever and Ashby.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from .base import SLEEP, clean, get_json

NAME = "rippling"
LIST = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
DETAIL = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}"


def probe(slug: str) -> int | None:
    d = get_json(LIST.format(slug=slug))
    return len(d) if isinstance(d, list) and d else None


def _description(desc) -> str:
    """
    Rippling splits the posting into {company, role} rather than one blob.

    Both halves are kept and the role text comes first: the company boilerplate
    is identical across every posting on a board, so leading with it would make
    every description look alike to anything reading them. The company section
    still carries the sponsorship and eligibility language, which is the part
    that decides a visa verdict, so it cannot simply be dropped.
    """
    if isinstance(desc, dict):
        return clean(" ".join(filter(None, (desc.get("role"), desc.get("company")))))
    return clean(desc)


def _loc(job: dict) -> str | None:
    """The list gives one location, the detail gives all of them."""
    one = job.get("workLocation") or {}
    many = job.get("workLocations") or []
    labels = [x.get("label") for x in many if isinstance(x, dict) and x.get("label")]
    if not labels and one.get("label"):
        labels = [one["label"]]
    return "; ".join(labels) or None


def fetch(slug: str, limit: int | None = None,
          with_descriptions: bool = True, workers: int = 8, **_) -> list[dict]:
    d = get_json(LIST.format(slug=slug))
    if not isinstance(d, list):
        return []
    jobs = d[:limit] if limit else d

    out = [{
        "board": NAME,
        "job_id": str(j.get("uuid")),
        "title": j.get("name"),
        "location": _loc(j),
        "description": "",
        "url": j.get("url"),
        "posted": (j.get("createdOn") or "")[:10] or None,
        "department": (j.get("department") or {}).get("label"),
    } for j in jobs]

    # The list omits the description, so full text costs one request per
    # posting - fetched concurrently, since it is entirely network-bound.
    if with_descriptions and out:
        def detail(p):
            j = get_json(DETAIL.format(slug=slug, uuid=p["job_id"]))
            if j:
                p["description"] = _description(j.get("description"))
                p["location"] = _loc(j) or p["location"]
            time.sleep(SLEEP)
            return p
        with ThreadPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(detail, out))
    return out
