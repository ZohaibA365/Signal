"""
Oracle Cloud Recruiting (the "CX" candidate experience).

Common among large enterprises that run Oracle HCM - Oracle itself, American
Express and others. Worth an adapter because those employers are large and
appear in the corpus only through an aggregator otherwise, which means a link
that cannot be clicked.

Coordinates are the pod host and the site number, both read off a real careers
URL. Oracle's own board is `eeho.fa.us2.oraclecloud.com` with site `CX_1` -
the pod name is opaque and no more guessable than a Workday tenant.

The list endpoint omits the description, so full text costs one request per
posting and is off by default, as with Workday and SmartRecruiters.
"""

from __future__ import annotations

import time

from .base import SLEEP, clean, get_json

NAME = "oraclecloud"
PAGE = 200
MAX_PAGES = 40

LIST = "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAIL = "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"


def _list_page(host: str, site: str, offset: int, limit: int) -> dict | None:
    return get_json(LIST.format(host=host), params={
        "onlyData": "true",
        "expand": "requisitionList.secondaryLocations",
        "finder": (f"findReqs;siteNumber={site},limit={limit},"
                   f"offset={offset},sortBy=POSTING_DATES_DESC"),
    })


def _unwrap(d: dict | None) -> tuple[list, int]:
    """The payload nests the list one level down under a single item."""
    items = (d or {}).get("items") or []
    if not items:
        return [], 0
    return items[0].get("requisitionList") or [], items[0].get("TotalJobsCount") or 0


def probe(host: str, site: str = "CX_1", **_) -> int | None:
    _, total = _unwrap(_list_page(host, site, 0, 1))
    return total or None


def fetch(host: str, site: str = "CX_1", limit: int | None = None,
          with_descriptions: bool = False, **_) -> list[dict]:
    out: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        batch, total = _unwrap(_list_page(host, site, offset, PAGE))
        if not batch:
            break
        for j in batch:
            jid = str(j.get("Id"))
            out.append({
                "board": NAME,
                "job_id": jid,
                "title": j.get("Title"),
                "location": j.get("PrimaryLocation"),
                "description": "",
                "url": (f"https://{host}/hcmUI/CandidateExperience/en/sites/"
                        f"{site}/job/{jid}"),
                "posted": (j.get("PostedDate") or "")[:10] or None,
                "department": j.get("JobFamily") or j.get("JobFunction"),
            })
        offset += PAGE
        if (limit and len(out) >= limit) or offset >= total:
            break
        time.sleep(SLEEP)

    if limit:
        out = out[:limit]
    if with_descriptions:
        for p in out:
            d = get_json(DETAIL.format(host=host), params={
                "expand": "all", "onlyData": "true",
                "finder": f"ById;Id={p['job_id']},siteNumber={site}"})
            item = ((d or {}).get("items") or [{}])[0]
            p["description"] = clean(item.get("ExternalDescriptionStr"))
            p["location"] = item.get("PrimaryLocation") or p["location"]
            time.sleep(SLEEP)
    return out
