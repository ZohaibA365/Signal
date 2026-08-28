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
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "storage"))
sys.path.insert(0, str(HERE))

from db import connect  # noqa: E402
import queries as Q  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("build")

DIST = HERE / "dist"
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


def league_table(rows, label_key, value_key, link=None, limit=8) -> Markup:
    """
    Server-rendered bar table.

    Bars are inline HTML rather than a charting library: they paint with the
    document, work with JavaScript disabled, and add no CDN dependency that can
    break or slow the page.
    """
    rows = rows[:limit]
    if not rows:
        return Markup("")
    # Postgres NUMERIC comes back as Decimal, which will not mix with float in
    # arithmetic. Coerce once here rather than at every call site.
    def num(r):
        v = r[value_key]
        return float(v) if v is not None else 0.0

    top = max(num(r) for r in rows) or 1.0
    out = ['<div class="league">']
    for i, r in enumerate(rows):
        val = num(r)
        pct = 100.0 * val / top
        name = r[label_key]
        if link:
            name = f'<a href="{link(r)}">{name}</a>'
        shown = f"{int(val):,}" if float(val).is_integer() and val > 100 else f"{val:g}%"
        muted = " muted" if i >= 3 else ""
        out.append(
            f'<div class="row"><span class="name">{name}</span>'
            f'<span class="track-cell"><span class="track">'
            f'<span class="fill{muted}" style="width:{pct:.1f}%"></span></span></span>'
            f'<span class="fig">{shown}</span></div>'
        )
    out.append("</div>")
    return Markup("".join(out))


def build(skip_pages: bool = False) -> None:
    started = time.time()
    conn = connect(autocommit=True)
    cur = conn.cursor()
    log.info("Querying warehouse")
    data = fetch_all(cur)
    conn.close()

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

    rows_out = []
    for r in search_rows:
        pi, path = split_url(r["redirect_url"])
        rows_out.append({
            "t": r["job_title"], "c": r["company_name"], "s": r["location_state"],
            "n": r["country"], "l": r["seniority"], "d": r["days_since_posted"],
            "w": int(r["salary_min_reported"]) if r["salary_min_reported"] else None,
            "h": pi, "u": path, "f": r["fit_score"], "e": r["eligibility"],
            "p": r["sponsorship_status"],
            "k": (r["techs"] or "").split(",") if r["techs"] else [],
        })
    payload = {"prefixes": prefixes, "rows": rows_out}

    (DIST / "data").mkdir(exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    (DIST / "data" / "jobs.json").write_bytes(raw)
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
    fresh = data["FRESHNESS"][0]
    common = {
        "stats": stats, "freshness": fresh, "site_url": SITE_URL, "repo_url": REPO_URL,
        "generated_at": datetime.now(timezone.utc).strftime("%d %b %Y"),
    }

    def render(template: str, out: Path, **ctx) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(env.get_template(template).render(**common, **ctx))

    # ---- homepage --------------------------------------------------------
    demand = data["DEMAND_BY_CATEGORY"]
    warehouses = [r for r in demand if r["category"] == "warehouse"]
    wh_share = round(sum(r["pct_of_category"] for r in warehouses[:2]))
    salary = data["SALARY_LEADERS"]
    sponsors = sorted(
        (r for r in data["COMPANIES"] if r["total_filings"]),
        key=lambda r: -r["total_filings"])[:8]
    for s in sponsors:
        s["slug"] = slugify(s["company_name"])

    render("home.html", DIST / "index.html", nav="home", rel="",
           canonical="/",
           page_title="Signal — the US data & AI job market index",
           page_description=(f"Daily index of {stats['postings']:,} US data and AI job "
                             f"postings: demand by technology, what each skill pays, and "
                             f"which employers verifiably sponsor visas."),
           warehouse_table=league_table(warehouses, "tech_name", "openings",
                                        link=lambda r: f"tech/{r['tech_slug']}/"),
           warehouse_share=wh_share,
           salary_table=league_table(salary, "tech_name", "pct_top_band",
                                     link=lambda r: f"tech/{r['tech_slug']}/"),
           salary_leaders_names=", ".join(r["tech_name"] for r in salary[:3]),
           stack_pairs=data["STACK_PAIRS"][:6],
           top_sponsors=sponsors,
           search_count=len(rows_out),
           company_count=len(data["COMPANIES"]))

    # ---- search ----------------------------------------------------------
    render("search.html", DIST / "search" / "index.html", nav="search", rel="../",
           canonical="/search/",
           page_title="Find a data or AI job — Signal",
           page_description=(f"Search {len(rows_out):,} US and Canadian data and AI roles by "
                             f"skill, level, location and verified visa sponsorship."),
           search_count=len(rows_out))

    # ---- company pages ---------------------------------------------------
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
               c=c, techs=techs, roles=roles,
               vocab_size=len({r["tech_slug"] for r in data["COMPANY_TECH"]}),
               tech_table=league_table(techs, "tech_name", "mentions",
                                       link=lambda r: f"../../tech/{r['tech_slug']}/", limit=10))

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
               t=t, employers=emp, category_size=cat_sizes[t["category"]],
               pairs=pairs_by_tech.get(t["tech_slug"], [])[:8],
               employer_table=league_table(emp, "company_name", "postings", limit=8))

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
    render("methodology.html", DIST / "methodology" / "index.html",
           nav="methodology", rel="../", canonical="/methodology/",
           page_title="How Signal is built — methodology | Signal",
           page_description=("The pipeline behind Signal, what the data can and cannot "
                             "support, and the errors caught before publishing."),
           search_count=len(rows_out), company_count=len(companies))

    # ---- sitemap / robots -------------------------------------------------
    urls = ["/", "/search/", "/companies/", "/tech/", "/methodology/"]
    urls += [f"/companies/{c['slug']}/" for c in companies]
    urls += [f"/tech/{t['tech_slug']}/" for t in techs]
    today = datetime.now(timezone.utc).date()
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
