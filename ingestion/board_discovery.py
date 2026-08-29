"""
Find which applicant-tracking system each employer's board lives on.

Why this is a separate, cached, resumable job rather than part of ingestion:
board coordinates are not derivable from a company name. Guessing slugs
against Greenhouse, Lever and Ashby resolved 5 of the 60 largest employers in
the corpus. The other 55 are on Workday or SmartRecruiters, where the
coordinates are arbitrary strings - `capitalone.wd12` + `Capital_One`, and
`BoschGroup` rather than `bosch`. Those have to be read off a real careers
URL, which is a human-or-search step, done once, recorded in boards.yml.

So discovery runs in two passes:

  1. Guess - cheap, automated, one request per slug variant per board. Good
     for the startup tail.
  2. Manual - a careers URL is looked up, parsed into coordinates, verified,
     and committed to boards.yml. Good for the enterprises that hold most of
     the postings.

Both write to the same registry, and every attempt is cached. Caching the
FAILURES matters as much as caching the successes: a sweep over 4,000
employers that re-probes what it already answered cannot be resumed and
wastes a politeness budget it does not have.

Usage:
    python ingestion/board_discovery.py --top 200        # sweep by volume
    python ingestion/board_discovery.py --company "Figma"
    python ingestion/board_discovery.py --load-manual    # apply boards.yml
    python ingestion/board_discovery.py --report
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "storage"))
from ats import ADAPTERS, GUESSABLE  # noqa: E402
from ats.base import get_json, slug_variants  # noqa: E402
from db import connect as _connect  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("board_discovery")

MANUAL_FILE = Path(__file__).with_name("boards.yml")

# Employers that are staffing agencies rather than end employers. Their
# postings are real but they are not companies anyone cold-emails, and they
# have no career board worth resolving.
AGENCY_RE = re.compile(
    r"staffing|recruit|talent|consult|solutions|technologies inc|systems inc|"
    r"resourc|placement|search group|agency|staffmark|robert half|insight global",
    re.I)

LEGAL_RE = re.compile(r"\b(inc|llc|ltd|corp|corporation|company|co|plc|gmbh|sa|nv)\b\.?", re.I)


def canon(name: str) -> str:
    """Comparable form of a company name: no legal suffix, no punctuation."""
    return re.sub(r"[^a-z0-9]", "", LEGAL_RE.sub("", (name or "").lower()))


# --------------------------------------------------------------------- verify

def board_name(ats: str, coords: dict) -> str | None:
    """The company name the board itself claims, where the API exposes one."""
    if ats == "greenhouse":
        d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{coords['slug']}")
        return (d or {}).get("name")
    return None


def verify(company: str, ats: str, coords: dict) -> str | None:
    """
    Decide whether a guessed board really belongs to this company.

    Returns a confidence grade, or None to reject. Greenhouse publishes the
    board's company name, so a match there is real evidence. The other boards
    publish nothing, leaving only the fact that the slug was derived from the
    company name - which is a weaker claim, and is graded as such rather than
    being quietly treated as equivalent.
    """
    claimed = board_name(ats, coords)
    if claimed:
        a, b = canon(claimed), canon(company)
        if a == b or a.startswith(b) or b.startswith(a):
            return "name_verified"
        log.debug("    rejected %s: board says %r, we asked for %r", ats, claimed, company)
        return None
    slug = canon(coords.get("slug", ""))
    return "token_match" if slug and slug in canon(company) else None


def confirm(ats: str, slug: str) -> int:
    """
    Prove the board again with a real fetch, and count what it actually returns.

    A single probe is not enough. Sweeping the 120 largest employers produced
    a Greenhouse board for "microsoft" that reported 2 postings and, re-read
    minutes later, held none and had no metadata at all - a flaky or abandoned
    board that would have published as Microsoft's. Requiring a second,
    different call to return a usable posting removes that class of entry.
    """
    try:
        jobs = ADAPTERS[ats].fetch(slug=slug, limit=5)[:5]
    except Exception:
        return 0
    usable = [j for j in jobs if j.get("url") and j.get("title")]
    if not usable:
        return 0
    # The posting's own URL must point back at the board we asked for,
    # otherwise the adapter resolved somewhere unexpected.
    if not any(slug.lower() in (j["url"] or "").lower() for j in usable):
        return 0
    return len(jobs)


def resolve(company: str) -> dict:
    """Try every guessable board for one company. Cheapest boards first."""
    for slug in slug_variants(company):
        for ats in GUESSABLE:
            try:
                n = ADAPTERS[ats].probe(slug)
            except Exception:
                n = None
            if not n:
                continue
            grade = verify(company, ats, {"slug": slug})
            if not grade:
                continue
            if not confirm(ats, slug):
                log.debug("    %s: %s:%s probed %s but returned nothing on re-read",
                          company, ats, slug, n)
                continue
            return {"company_name": company, "ats": ats, "coords": {"slug": slug},
                    "status": "resolved", "confidence": grade,
                    "discovered_via": "guess", "postings_seen": n, "note": None}
    return {"company_name": company, "ats": None, "coords": None,
            "status": "not_found", "confidence": None, "discovered_via": "guess",
            "postings_seen": None,
            "note": f"tried {','.join(slug_variants(company))} on {','.join(GUESSABLE)}"}


# ------------------------------------------------------------------- registry

UPSERT = """
INSERT INTO board_registry (company_name, ats, coords, status, confidence,
                            discovered_via, postings_seen, note, verified_at)
VALUES (%(company_name)s, %(ats)s, %(coords)s, %(status)s, %(confidence)s,
        %(discovered_via)s, %(postings_seen)s, %(note)s, NOW())
ON CONFLICT (company_name) DO UPDATE SET
    ats = EXCLUDED.ats, coords = EXCLUDED.coords, status = EXCLUDED.status,
    confidence = EXCLUDED.confidence, discovered_via = EXCLUDED.discovered_via,
    postings_seen = EXCLUDED.postings_seen, note = EXCLUDED.note,
    verified_at = NOW()
"""


def connect():
    """The shared warehouse connection, so this job targets the same database
    as dbt and the site rather than whatever POSTGRES_* happens to be set."""
    return _connect(cursor_factory=RealDictCursor)


# Results are written in batches rather than once at the end. A sweep of a few
# hundred companies takes long enough that the warehouse - Neon, which is
# serverless - closes an idle connection underneath it: a 375-company run
# probed for twelve minutes and then lost every result to "SSL connection has
# been closed unexpectedly". Saving as we go also makes an interrupted sweep
# resumable, since anything already written is cached and skipped next time.
SAVE_EVERY = 25


def save(conn, entries: list[dict]) -> None:
    with conn, conn.cursor() as cur:
        for e in entries:
            cur.execute(UPSERT, {**e, "coords": Json(e["coords"]) if e["coords"] else None})


def already_tried(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT company_name FROM board_registry")
        return {r["company_name"] for r in cur.fetchall()}


def targets_by_volume(conn, limit: int) -> list[str]:
    """Employers worth resolving, largest first - that is where the leverage is."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT company_name, count(*) n FROM raw_postings
            WHERE company_name IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT %s""", (limit * 3,))
        rows = cur.fetchall()
    return [r["company_name"] for r in rows if not AGENCY_RE.search(r["company_name"])][:limit]


# --------------------------------------------------------------------- manual

def load_manual(conn) -> int:
    """
    Apply boards.yml - the hand-resolved entries.

    Each is verified against the live board before it is stored, so a typo in
    a tenant or site path fails loudly here rather than silently attaching an
    empty board to a company.
    """
    if not MANUAL_FILE.exists():
        log.warning("no %s", MANUAL_FILE.name)
        return 0
    spec = yaml.safe_load(MANUAL_FILE.read_text()) or {}
    entries = []
    for company, cfg in (spec.get("companies") or {}).items():
        ats = cfg["ats"]
        # Everything except the adapter name and the human note IS a
        # coordinate, so a new adapter needs no change here.
        coords = {k: v for k, v in cfg.items() if k not in ("ats", "note")}
        try:
            n = ADAPTERS[ats].probe(**coords)
        except Exception as exc:
            n = None
            log.warning("  %-28s %s probe raised %s", company, ats, type(exc).__name__)
        if not n:
            log.warning("  %-28s %s REJECTED - board empty or coordinates wrong",
                        company, ats)
            continue
        log.info("  %-28s %-16s %6d postings", company, ats, n)
        entries.append({"company_name": company, "ats": ats, "coords": coords,
                        "status": "resolved", "confidence": "manual",
                        "discovered_via": "manual", "postings_seen": n,
                        "note": cfg.get("note")})
    save(conn, entries)
    return len(entries)


# ------------------------------------------------------------------------ cli

def report(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT status, confidence, count(*) n, sum(postings_seen) postings
            FROM board_registry GROUP BY 1,2 ORDER BY 1,2""")
        rows = cur.fetchall()
    log.info("registry:")
    for r in rows:
        log.info("  %-12s %-14s %4d companies  %s board postings",
                 r["status"], r["confidence"] or "-", r["n"],
                 f"{r['postings']:,}" if r["postings"] else "-")


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolve company career boards")
    ap.add_argument("--top", type=int, help="sweep the N largest employers")
    ap.add_argument("--company", nargs="+", help="resolve specific companies")
    ap.add_argument("--load-manual", action="store_true", help="apply boards.yml")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-probe names previously recorded as not_found")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    conn = connect()
    try:
        if args.load_manual:
            log.info("applying %s", MANUAL_FILE.name)
            log.info("loaded %d manual entries", load_manual(conn))

        names: list[str] = list(args.company or [])
        if args.top:
            names += targets_by_volume(conn, args.top)
        if names:
            if not args.retry_failed:
                seen = already_tried(conn)
                skipped = [n for n in names if n in seen]
                names = [n for n in names if n not in seen]
                if skipped:
                    log.info("skipping %d already answered", len(skipped))
            log.info("probing %d companies", len(names))
            done, hits = 0, []
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                pending, batch = ex.map(resolve, names), []
                for r in pending:
                    batch.append(r)
                    done += 1
                    if r["status"] == "resolved":
                        hits.append(r)
                    if len(batch) >= SAVE_EVERY:
                        # A fresh connection per batch: the sweep outlives any
                        # single one the warehouse is willing to hold open.
                        conn = connect()
                        try:
                            save(conn, batch)
                        finally:
                            conn.close()
                        log.info("  ... %d/%d probed, %d resolved so far",
                                 done, len(names), len(hits))
                        batch = []
                if batch:
                    conn = connect()
                    try:
                        save(conn, batch)
                    finally:
                        conn.close()
            for r in sorted(hits, key=lambda r: -(r["postings_seen"] or 0)):
                log.info("  %-30s %-16s %-14s %5d",
                         r["company_name"], r["ats"], r["confidence"], r["postings_seen"])
            log.info("resolved %d of %d", len(hits), len(names))
            conn = connect()

        if args.report or not (names or args.load_manual):
            report(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
