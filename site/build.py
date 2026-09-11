"""
Generate the static site from the warehouse.

One process: query everything once, render every page from memory, write to
dist/. Roughly 500 pages in a few seconds, with no per-page query - a query per
page would turn this into minutes and hammer the warehouse for nothing.

Static output is a deliberate choice over a running app. A dashboard server
sleeps, cold-starts, and costs money per visitor; static HTML paints instantly,
costs nothing, and is indexable. Search still works because the searchable
projection of every posting is ~250 kB gzipped and ships to the browser, so
filtering happens client-side with no backend at all.

Usage:
    python site/build.py                 # full build
    python site/build.py --skip-pages    # data payload only, for iterating on JS
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "storage"))
sys.path.insert(0, str(HERE))

import queries as Q  # noqa: E402
from db import connect, describe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("build")

DIST = HERE / "dist"
# Below this many days of collection, month-over-month comparisons
# describe our ingest history rather than the market.
#
# Retained as the fallback only. The authoritative figure now comes from
# hist_coverage, which counts days a real daily archive run actually covered -
# the question this constant never asked. It compared distinct
# market_snapshots.snapshot_date, while outreach/insights.py compared distinct
# raw_postings.first_seen::date under the same name, and neither knew whether a
# day's posting panel was complete.
MIN_DAYS_FOR_TREND = 45
SITE_URL = os.getenv("SITE_URL", "https://signal-jobs.dev")
REPO_URL = "https://github.com/ZohaibA365/Signal"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unknown"


def fetch_all(cur) -> dict:
    """Run every query once. Empty results fail the build rather than silently
    publishing an empty page - a stale or blank site is worse than none."""
    data = {}
    for name in (n for n in dir(Q) if n.isupper()):
        cur.execute(getattr(Q, name))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        if not rows:
            raise SystemExit(f"Query {name} returned no rows - refusing to build a blank site")
        data[name] = rows
        log.info("  %-22s %s rows", name, f"{len(rows):,}")
    return data


def fetch_optional(cur, data: dict) -> None:
    """
    Fetch the history queries, which may legitimately return nothing.

    fetch_all() refuses to build when a query is empty, and that is right for
    the core data. It is wrong for history: the panel only becomes usable after
    enough complete days accumulate, so empty has to mean "say so on the page",
    not "fail the build". The tables may also not exist yet on a warehouse that
    has not run analytics/build_history.py, which must be equally survivable.
    """
    for name, sql in Q.may_be_empty.items():
        try:
            cur.execute(sql)
            cols = [c.name for c in cur.description]
            data[name] = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        except Exception as exc:                      # noqa: BLE001 - any DB error
            cur.connection.rollback()
            data[name] = []
            log.warning("  %-22s unavailable (%s)", name, str(exc).strip()[:80])
            continue
        log.info("  %-22s %s rows%s", name, f"{len(data[name]):,}",
                 "  (no history yet)" if not data[name] else "")


def peer_summary(peers: list[dict], c: dict) -> dict | None:
    """
    How a company compares to its peer set on hiring pace.

    Returns None when there are no peers - the page then shows no comparison
    at all, which is the right outcome. A wrong peer claim on a page the
    company itself may read is worse than a missing section.
    """
    if not peers:
        return None
    last30 = sorted(p["postings_last_30d"] or 0 for p in peers)
    median = last30[len(last30) // 2]
    own = c["postings_last_30d"] or 0
    ratio = (own / median) if median else None
    return {
        "count": len(peers),
        "median_last_30d": median,
        "own_last_30d": own,
        "ratio": round(ratio, 1) if ratio else None,
        "faster": ratio is not None and ratio >= 1.3,
        "slower": ratio is not None and ratio <= 0.7,
        "names": ", ".join(p["peer_name"] for p in peers[:3]),
    }


def headline_for(c: dict, peers: list[dict], trend_ok: bool) -> str | None:
    """
    The single strongest true observation about a company.

    NO MONTH-OVER-MONTH CLAIMS until the corpus can support them. An earlier
    version produced "Google's hiring is up 2600%", which is false: job boards
    delist filled roles, so a freshly collected corpus always shows far more
    recent postings than older ones. Google's prior window held 3 postings
    against 81 recent, and corpus-wide the same artifact reads as 1.9x. With
    only a handful of collection days behind it, a pace claim measures when we
    started collecting, not how a company is hiring.

    That is the same failure as the "Python demand fell 70%" claim caught in
    the trend model. Counts, shares and peer-relative comparisons survive it,
    because a comparison between two companies carries the bias on both sides
    and cancels most of it. Absolute change over time does not.
    """
    total = c["total_postings"] or 0
    last30 = c["postings_last_30d"] or 0

    if trend_ok and (c["postings_prior_30d"] or 0) >= 10:
        prior = c["postings_prior_30d"]
        change = round(100.0 * (last30 - prior) / prior)
        if abs(change) >= 50:
            direction = "accelerated" if change > 0 else "slowed"
            return (f"Hiring has {direction}: {last30} roles posted in the last 30 days, "
                    f"{'up' if change > 0 else 'down'} {abs(change)}% on the month before.")

    if c["total_filings"] and c["total_filings"] >= 20:
        return (f"Has filed {c['total_filings']:,} US visa applications, so sponsorship "
                f"is a matter of record rather than inference.")
    if (c["distinct_states"] or 0) >= 15:
        return f"Hiring across {c['distinct_states']} states."
    if total >= 40:
        return f"{total:,} tracked roles, {last30} of them posted in the last 30 days."
    return None


def sparkline(rows: list[dict], key: str, width: int = 560, height: int = 90) -> dict | None:
    """
    An inline SVG polyline for a measured daily series.

    Inline and hand-built because the site ships no JavaScript charting library
    and should not start: the series is at most a few hundred points, and a
    polyline is a handful of bytes against a charting bundle.

    The y axis deliberately starts at the series minimum rather than zero. That
    exaggerates variation, which would be misleading for a headline number, so
    the caller renders the first and last values as text beside it - the shape
    is the claim, the numbers are the evidence.
    """
    if len(rows) < 2:
        return None
    values = [r[key] or 0 for r in rows]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / (len(values) - 1)
    points = " ".join(
        f"{i * step:.1f},{height - ((v - lo) / span) * height:.1f}"
        for i, v in enumerate(values)
    )
    return {
        "points": points, "width": width, "height": height,
        "first": values[0], "last": values[-1],
        "low": lo, "high": hi, "days": len(values),
        "start": rows[0]["observed_date"], "end": rows[-1]["observed_date"],
    }


def with_bars(rows: list[dict], key: str, limit: int | None = None) -> list[dict]:
    """Attach a 0-100 bar width relative to the largest value in the group."""
    rows = rows[:limit] if limit else rows
    if not rows:
        return []
    # Postgres NUMERIC arrives as Decimal, which will not mix with float.
    top = max(float(r[key] or 0) for r in rows) or 1.0
    for r in rows:
        r["bar"] = round(100.0 * float(r[key] or 0) / top, 1)
    return rows


def build(skip_pages: bool = False) -> None:
    started = time.time()

    # Without an explicit connection URL, db.connect() falls back to localhost.
    # That is right for development and actively misleading in CI, where there
    # is no local Postgres: the build would fail on a missing relation rather
    # than naming the real problem, which is an absent credential.
    if os.getenv("CI") and not (os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")):
        raise SystemExit(
            "DATABASE_URL is not set. The site is generated from the warehouse, so "
            "the build needs a connection string. Add it as a repository secret "
            "under Settings -> Secrets and variables -> Actions."
        )

    conn = connect(autocommit=True)
    cur = conn.cursor()
    log.info("Querying %s", describe())
    data = fetch_all(cur)
    fetch_optional(cur, data)
    conn.close()

    # Clear the output first. Without this, pages removed from the site linger
    # in dist and are published anyway - the old /search/ and /methodology/
    # routes survived a restructure and would have shipped as dead pages.
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "static" / "style.css", DIST / "style.css")
    if (HERE / "static" / "search.js").exists():
        shutil.copy(HERE / "static" / "search.js", DIST / "search.js")
    if (HERE / "static" / "favicon.svg").exists():
        shutil.copy(HERE / "static" / "favicon.svg", DIST / "favicon.svg")

    # ---- search payload -------------------------------------------------
    search_rows = data["SEARCH_ROWS"]
    # URLs were 48% of the payload. Ten distinct prefixes cover all ~10k rows
    # (8,788 share a single one), so they become a lookup table and each row
    # keeps only an index plus the path. Field names are single letters for the
    # same reason: this file is transferred, not read by a person.
    prefixes: list[str] = []

    def split_url(u):
        if not u or "//" not in u:
            return None, u
        head = "/".join(u.split("/")[:3]) + "/"
        if head not in prefixes:
            prefixes.append(head)
        return prefixes.index(head), u[len(head):]

    # Categorical fields are dictionary-encoded: company, state, seniority,
    # eligibility, sponsorship and technology are a few hundred distinct
    # strings repeated across twelve thousand rows. Storing an index instead
    # of the string cuts the payload the browser has to parse and hold, which
    # matters more than the transfer size - gzip already collapses the
    # repetition on the wire, but the parsed objects live in memory on a
    # phone.
    dicts: dict[str, list[str]] = {k: [] for k in ("c", "s", "l", "e", "p", "k")}
    index: dict[str, dict[str, int]] = {k: {} for k in dicts}

    def code(field: str, value):
        """Index for a value, assigning one on first sight. None stays None."""
        if value is None or value == "":
            return None
        table, seen = dicts[field], index[field]
        if value not in seen:
            seen[value] = len(table)
            table.append(value)
        return seen[value]

    rows_out = []
    for r in search_rows:
        pi, path = split_url(r["link_url"])
        rows_out.append({
            "t": r["job_title"], "c": code("c", r["company_name"]),
            "s": code("s", r["location_state"]),
            "n": r["country"], "l": code("l", r["seniority"]),
            "d": r["days_since_posted"],
            "w": int(r["salary_min_reported"]) if r["salary_min_reported"] else None,
            "h": pi, "u": path, "f": r["fit_score"], "e": code("e", r["eligibility"]),
            "p": code("p", r["posting_sponsorship"]),
            "v": r["sponsor_filings"] or 0, "r": 1 if r["is_remote"] else 0,
            "k": [code("k", t) for t in (r["techs"] or "").split(",") if t],
        })
    payload = {"prefixes": prefixes, "dicts": dicts, "rows": rows_out}
    searchable_roles = len(rows_out)

    (DIST / "data").mkdir(exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    (DIST / "data" / "jobs.json").write_bytes(raw)
    # A build stamp shared by the script tag and the payload request.
    #
    # The payload format changed once already - rows went from carrying
    # strings to carrying dictionary indices - and a browser holding the old
    # cached script against the new payload fails exactly like a broken site.
    # Pinning both to the same hash means a cached script can never be paired
    # with a payload it does not understand: either both come from cache or
    # both are refetched.
    payload_hash = hashlib.sha256(raw).hexdigest()[:12]
    gz = gzip.compress(raw, 9)
    log.info("Search payload: %s roles, %.0f kB raw, %.0f kB gzipped",
             f"{len(rows_out):,}", len(raw) / 1024, len(gz) / 1024)

    if skip_pages:
        log.info("--skip-pages: stopping after payload")
        return

    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.filters["fmt"] = lambda v: f"{int(v):,}" if v is not None else "—"

    stats = data["CORPUS_STATS"][0]
    # Surfaced on the page: the search set is capped, and saying so is better
    # than letting someone conclude a missing job means the site is broken.
    stats["searchable_roles"] = searchable_roles
    stats["build_hash"] = payload_hash
    fresh = data["FRESHNESS"][0]
    # How many distinct days we have actually collected on. Trend claims are
    # gated on this: with a short history, "last 30 days vs the 30 before"
    # measures the collection start date rather than the market.
    collection_days = fresh.get("days_of_history") or 0
    # hist_coverage wins when it exists: it is the only source that distinguishes
    # a day that was measured from a date that merely appears in the panel.
    cov = (data.get("HISTORY_COVERAGE") or [{}])[0]
    if cov:
        trend_ok = bool(cov.get("trend_is_publishable"))
        history_days = cov.get("measured_days") or 0
    else:
        trend_ok = collection_days >= MIN_DAYS_FOR_TREND
        history_days = collection_days
    log.info("  history: %s measured day(s), trend %s",
             history_days, "publishable" if trend_ok else "suppressed")

    pace_by_company = {r["company_name"]: r for r in data.get("COMPANY_PACE", [])}
    history_by_tech: dict[str, list[dict]] = {}
    for r in data.get("TECH_HISTORY", []):
        history_by_tech.setdefault(r["tech_slug"], []).append(r)
    common = {
        "stats": stats, "freshness": fresh, "site_url": SITE_URL, "repo_url": REPO_URL,
        "coverage": cov, "history_days": history_days, "trend_ok": trend_ok,
        "generated_at": datetime.now(UTC).strftime("%d %b %Y"),
    }

    def render(template: str, out: Path, **ctx) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(env.get_template(template).render(**common, **ctx))

    # ---- homepage: the job search itself. This is a job board first. -----
    demand = data["DEMAND_BY_CATEGORY"]
    salary = data["SALARY_LEADERS"]

    render("jobs.html", DIST / "index.html", nav="jobs", rel="", canonical="/",
           page_title="Signal — data & AI jobs in the US and Canada",
           page_description=(f"Search {len(rows_out):,} data and AI roles by skill, level, "
                             f"location and verified visa sponsorship. Updated daily."),
           search_count=len(rows_out))

    # ---- market index: the findings, one click away ----------------------
    by_cat: dict[str, list] = {}
    for r in demand:
        by_cat.setdefault(r["category"], []).append(r)
    ordered = sorted(by_cat.items(), key=lambda kv: -sum(x["openings"] for x in kv[1]))

    render("market.html", DIST / "market" / "index.html", nav="market", rel="../",
           canonical="/market/",
           page_title="US data & AI market index — demand, pay and stacks | Signal",
           page_description=(f"Daily demand across {stats['technologies']} technologies, "
                             f"what each skill pays, and which tools appear together."),
           demand_by_category=[(c, with_bars(rows, "openings", 10)) for c, rows in ordered],
           salary=salary[:15], pairs=data["STACK_PAIRS"][:12])

    # ---- company pages ---------------------------------------------------
    # Peer comparison and market position, grouped once rather than queried
    # per page. These are the sections that make a company page worth sending
    # to someone who works there.
    peers_by_company: dict[str, list] = {}
    for r in data["COMPANY_PEERS"]:
        r["slug"] = slugify(r["peer_name"])
        peers_by_company.setdefault(r["company_name"], []).append(r)
    market_by_company: dict[str, list] = {}
    for r in data["COMPANY_MARKET_POSITION"]:
        market_by_company.setdefault(r["company_name"], []).append(r)

    tech_by_company: dict[str, list] = {}
    for r in data["COMPANY_TECH"]:
        tech_by_company.setdefault(r["company_name"], []).append(r)
    roles_by_company: dict[str, list] = {}
    for r in data["COMPANY_ROLES"]:
        roles_by_company.setdefault(r["company_name"], []).append(r)

    companies = data["COMPANIES"]
    for c in companies:
        c["slug"] = slugify(c["company_name"])
        techs = sorted(tech_by_company.get(c["company_name"], []),
                       key=lambda r: -r["mentions"])[:10]
        roles = roles_by_company.get(c["company_name"], [])[:25]
        render("company.html", DIST / "companies" / c["slug"] / "index.html",
               nav="companies", rel="../../", canonical=f"/companies/{c['slug']}/",
               page_title=f"{c['company_name']} — hiring, stack and visa sponsorship | Signal",
               page_description=(
                   f"{c['company_name']}: {c['total_postings']:,} tracked data and AI roles, "
                   f"{c['postings_last_30d']} in the last 30 days"
                   + (f", {c['total_filings']:,} H-1B filings." if c["total_filings"] else ".")),
               c=c, techs=with_bars(techs, "mentions"), roles=roles,
               peers=peers_by_company.get(c["company_name"], []),
               peer_stats=peer_summary(peers_by_company.get(c["company_name"], []), c),
               market_pos=[m for m in market_by_company.get(c["company_name"], [])
                           if m["mentions"] >= 2][:8],
               headline=headline_for(c, peers_by_company.get(c["company_name"], []),
                                     trend_ok),
               pace=pace_by_company.get(c["company_name"]))

    render("list.html", DIST / "companies" / "index.html", nav="companies", rel="../",
           canonical="/companies/",
           page_title="Company hiring profiles — Signal",
           page_description=f"Hiring pace, tech stack and visa filing history for {len(companies)} employers.",
           heading="Companies",
           blurb=(f"{len(companies)} employers with at least five tracked roles. Hiring pace, "
                  f"technology stack, geography and verified visa filing history."),
           headers=["Company", "Roles", "Last 30d", "H-1B filings"],
           rows=[{"href": f"{c['slug']}/", "label": c["company_name"],
                  "cells": [f"{c['total_postings']:,}", f"{c['postings_last_30d']:,}",
                             f"{c['total_filings']:,}" if c["total_filings"] else "—"]}
                 for c in companies])

    # ---- technology pages ------------------------------------------------
    employers_by_tech: dict[str, list] = {}
    for r in data["TECH_EMPLOYERS"]:
        employers_by_tech.setdefault(r["tech_slug"], []).append(r)
    pairs_by_tech: dict[str, list] = {}
    for r in data["STACK_PAIRS"]:
        pairs_by_tech.setdefault(r["tech_slug"], []).append(r)

    techs = data["TECH_DETAIL"]
    cat_sizes = {}
    for t in techs:
        cat_sizes[t["category"]] = cat_sizes.get(t["category"], 0) + 1

    for t in techs:
        emp = employers_by_tech.get(t["tech_slug"], [])
        render("tech.html", DIST / "tech" / t["tech_slug"] / "index.html",
               nav="tech", rel="../../", canonical=f"/tech/{t['tech_slug']}/",
               page_title=f"{t['tech_name']} jobs — demand, pay and top employers | Signal",
               page_description=(
                   f"{t['openings']:,} US openings mention {t['tech_name']}. Demand rank, "
                   f"salary band, top employers and co-occurring tools."
                   if t["openings"] else
                   f"{t['tech_name']} in the US data and AI job market: "
                   f"{t['postings_mentioning']:,} postings and the tools it appears with."),
               t=t, employers=emp[:8], category_size=cat_sizes[t["category"]],
               pairs=pairs_by_tech.get(t["tech_slug"], [])[:8],
               history=history_by_tech.get(t["tech_slug"], []),
               spark=sparkline(history_by_tech.get(t["tech_slug"], []),
                               "postings_mentioning"))

    render("list.html", DIST / "tech" / "index.html", nav="tech", rel="../",
           canonical="/tech/",
           page_title="Technology demand index — Signal",
           page_description=f"Current US demand for {len(techs)} data and AI technologies.",
           heading="Technologies",
           blurb=(f"{len(techs)} technologies tracked daily. Openings are counted from the "
                  f"job board's market-wide index, so they do not depend on which postings "
                  f"Signal collected."),
           headers=["Technology", "Openings", "Category rank"],
           rows=[{"href": f"{t['tech_slug']}/", "label": t["tech_name"],
                  "cells": [f"{t['openings']:,}" if t["openings"] else "—",
                            f"#{t['category_rank']} in {t['category']}" if t["openings"]
                            else t["category"]]}
                 for t in techs])

    # ---- methodology -----------------------------------------------------
    render("method.html", DIST / "method" / "index.html",
           nav="method", rel="../", canonical="/method/",
           page_title="How Signal is built — methodology | Signal",
           page_description=("The pipeline behind Signal, what the data can and cannot "
                             "support, and the errors caught before publishing."),
           search_count=len(rows_out), company_count=len(companies))

    # ---- sitemap / robots -------------------------------------------------
    urls = ["/", "/market/", "/companies/", "/tech/", "/method/"]
    urls += [f"/companies/{c['slug']}/" for c in companies]
    urls += [f"/tech/{t['tech_slug']}/" for t in techs]
    today = datetime.now(UTC).date()
    (DIST / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{SITE_URL}{u}</loc><lastmod>{today}</lastmod></url>\n"
                  for u in urls)
        + "</urlset>\n")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

    log.info("Built %s pages in %.1fs -> %s", len(urls), time.time() - started, DIST)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the static site")
    ap.add_argument("--skip-pages", action="store_true")
    args = ap.parse_args()
    build(args.skip_pages)


if __name__ == "__main__":
    main()
