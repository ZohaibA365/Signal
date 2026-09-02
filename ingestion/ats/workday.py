"""
Workday adapter - the one that matters most for large employers.

Greenhouse and Lever skew to startups. The companies with the deepest US
hiring in this corpus - Capital One, Deloitte, PwC, Oracle, GM - are all on
Workday, which is why slug-guessing against the startup boards resolved only
5 of the 60 largest employers.

Workday exposes a public JSON search endpoint per tenant:

    POST /wday/cxs/{tenant}/{site}/jobs

The catch, and the reason discovery cannot be automated blindly: BOTH the
tenant host and the site path are arbitrary. Capital One is
`capitalone.wd12.myworkdayjobs.com` with site `Capital_One` - not `wd1`, not
`Careers`. Probing eight plausible tenants across six hosts and four common
site names resolved none of them. The values have to be read off the real
careers URL, which is what the registry stores.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta

from .base import SLEEP, clean, get_json, post_json

NAME = "workday"
PAGE = 20          # the endpoint caps a page at 20 regardless of what is asked
MAX_PAGES = 250    # a guard against a tenant that reports a nonsense total

# Matches every Workday careers URL shape seen in the wild, e.g.
#   https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One
#   https://foo.wd1.myworkdayjobs.com/External
URL_RE = re.compile(
    r"https?://(?P<tenant>[a-z0-9-]+)\.(?P<host>wd\d+)\.myworkdayjobs\.com"
    r"(?:/(?:[a-z]{2}-[A-Z]{2}))?/(?P<site>[A-Za-z0-9_-]+)")


def parse_url(url: str) -> dict | None:
    """Extract tenant/host/site from a careers URL. This is how entries are made."""
    m = URL_RE.search(url or "")
    return dict(tenant=m["tenant"], host=m["host"], site=m["site"]) if m else None


# Workday reports recency as prose - "Posted Today", "Posted 30+ Days Ago" -
# rather than a date, so it has to be resolved against the run date. "30+" is
# a floor, not a value: it is recorded as exactly 30 days and is the reason
# posting-age claims from Workday are treated as approximate.
_REL = re.compile(r"(\d+)\+?\s*day", re.I)


def parse_posted(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    now = datetime.now(UTC)
    if "today" in low:
        return now.date().isoformat()
    if "yesterday" in low:
        return (now - timedelta(days=1)).date().isoformat()
    m = _REL.search(low)
    if m:
        return (now - timedelta(days=int(m.group(1)))).date().isoformat()
    return None


def _api(tenant: str, host: str, site: str) -> str:
    return f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def _public(tenant: str, host: str, site: str, path: str) -> str:
    return f"https://{tenant}.{host}.myworkdayjobs.com/en-US/{site}{path}"


def probe(tenant: str, host: str, site: str) -> int | None:
    """Total postings on this board, or None if the coordinates are wrong."""
    d = post_json(_api(tenant, host, site),
                  {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    return d.get("total") if isinstance(d, dict) and d.get("total") is not None else None


def fetch(tenant: str, host: str, site: str, search: str = "",
          limit: int | None = None, with_descriptions: bool = False) -> list[dict]:
    """
    All postings on one Workday board.

    `with_descriptions` costs one extra request per posting, so it is off by
    default. It is worth paying selectively: full text is what makes
    sponsorship detection work, since Adzuna truncates at 500 characters and
    the visa boilerplate sits at the end of a posting.
    """
    out: list[dict] = []
    offset = 0
    total = None
    for _ in range(MAX_PAGES):
        d = post_json(_api(tenant, host, site),
                      {"appliedFacets": {}, "limit": PAGE, "offset": offset,
                       "searchText": search})
        if not isinstance(d, dict):
            break
        batch = d.get("jobPostings") or []
        if not batch:
            break
        # The count comes back only on the FIRST page; later pages report
        # total=0 while still returning results. Re-reading it each time made
        # the loop stop after page two, silently truncating every Workday
        # board to about 40 postings - RBC's 114 student roles came out as 40.
        if total is None:
            total = d.get("total") or 0
        for j in batch:
            path = j.get("externalPath") or ""
            # bulletFields carries the requisition id, which is stabler than
            # the URL path for identity.
            bullets = j.get("bulletFields") or []
            out.append({
                "board": NAME,
                "job_id": (bullets[0] if bullets else path.rsplit("/", 1)[-1]),
                "title": j.get("title"),
                "location": j.get("locationsText"),
                "description": "",
                "url": _public(tenant, host, site, path),
                "posted": parse_posted(j.get("postedOn")),
                "department": None,
                "_path": path,
            })
        offset += PAGE
        # Stop on a short page rather than on the reported total, which is
        # only trustworthy once.
        if (limit and len(out) >= limit) or len(batch) < PAGE or offset >= (total or 0):
            break
        time.sleep(SLEEP)

    if limit:
        out = out[:limit]
    if with_descriptions:
        for p in out:
            detail = get_json(
                f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
                f"{p.pop('_path', '')}")
            info = (detail or {}).get("jobPostingInfo") or {}
            p["description"] = clean(info.get("jobDescription"))
            # The list endpoint collapses multi-site roles to "6 Locations",
            # which is useless for the state filter; the detail carries the
            # real ones.
            locs = [info.get("location")] + list(info.get("additionalLocations") or [])
            locs = [x for x in locs if x]
            if locs:
                p["location"] = "; ".join(locs)
            time.sleep(SLEEP)
    else:
        for p in out:
            p.pop("_path", None)
    return out
