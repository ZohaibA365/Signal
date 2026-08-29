"""
Shared plumbing for applicant-tracking-system adapters.

Every adapter returns the same posting shape the original Greenhouse/Lever/
Ashby probes did, so everything downstream of ingestion stays untouched:

    {board, job_id, title, location, description, url, posted, department}

`url` is the contract that matters. It must be the employer's own posting,
reachable without a login and without a geo-block - that requirement is the
whole reason this package exists. Adzuna supplies 90% of the corpus and its
links are country-gated redirects that return 403 to anything that isn't a
browser, so they cannot be repaired in place and can only be replaced.
"""

from __future__ import annotations

import logging
import re
import time

import requests

log = logging.getLogger("ats")

TIMEOUT = 25
SLEEP = 0.3
MAX_RETRIES = 3

# Boards serve public JSON but several reject the python-requests default
# agent outright, so present as a browser.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json"})
# The discovery sweep runs a dozen threads against a handful of hosts, and the
# default pool of 10 per host silently discards and reopens connections under
# that load. Sizing the pool to the sweep keeps it to one connection per
# thread rather than a reconnect per request.
for _scheme in ("http://", "https://"):
    _session.mount(_scheme, requests.adapters.HTTPAdapter(
        pool_connections=32, pool_maxsize=32))


def request(method: str, url: str, **kw) -> requests.Response | None:
    """
    One HTTP call with the retry policy the ingesters already use.

    Returns None rather than raising: a board that is missing, renamed or
    rate-limiting is an ordinary outcome during a sweep of thousands of
    companies, not an error worth aborting on. 404 and 403 are returned
    immediately because retrying them only burns politeness budget.
    """
    kw.setdefault("timeout", TIMEOUT)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = _session.request(method, url, **kw)
        except requests.RequestException as exc:
            log.debug("    %s %s -> %s", method, url, type(exc).__name__)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r
        if r.status_code in (403, 404, 400, 401):
            return None
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return None
    return None


def get_json(url: str, **kw):
    r = request("GET", url, **kw)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def post_json(url: str, payload: dict, **kw):
    r = request("POST", url, json=payload, **kw)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def clean(html: str | None) -> str:
    """Board descriptions are HTML; the warehouse stores plain text."""
    if not html:
        return ""
    text = (html.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"'))
    return WS_RE.sub(" ", TAG_RE.sub(" ", text)).strip()


def slug_variants(name: str) -> list[str]:
    """
    Candidate board slugs for a company name.

    Cheap variants resolve most startups. They do NOT resolve enterprises:
    measured against the 60 largest employers in the corpus, guessing found
    only 5. That gap is what the registry and manual resolution exist for.
    """
    base = name.strip().lower()
    stripped = re.sub(r"\b(inc|llc|ltd|corp|corporation|company|technologies|labs|the)\b",
                      "", base).strip()
    out, seen = [], set()
    for v in (re.sub(r"[^a-z0-9]", "", base),
              re.sub(r"[^a-z0-9]+", "-", base).strip("-"),
              re.sub(r"[^a-z0-9]", "", stripped),
              re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")):
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out
