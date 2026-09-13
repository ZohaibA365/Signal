"""
The queue of employer matches a person has to decide, and the way to record them.

Nothing the machine infers from a name alone is published. An exact match after
normalisation is stated as fact; a multi-word brand prefixing exactly one legal
entity is stated as fact; everything else - a generic single word, or a brand
prefixing several entities - is held back and ends up here.

That holding back is deliberate and it is the reason this file exists. "Lucid
Motors" prefixes LUCID, which has 842 filings and is a different company from
Lucid Group with 15. "Cognizant" prefixed four entities and the first version took
the alphabetically first, claiming 13 filings for a company that has 15,355. Both
are plausible guesses and both are wrong, and a wrong one becomes a sponsorship
claim on a public page read by somebody deciding where to spend an application.

So the machine orders the queue and states the evidence, and a person decides. The
ordering is by postings at risk, because that is what a visitor experiences: the
top of this queue is where the withheld evidence costs the most.

Decisions go into storage/employer_aliases.csv, which already holds exactly this -
accepted and rejected pairs with a note on why - rather than into a second store
that could disagree with the first. A rejection is recorded as firmly as an
acceptance, so a pair judged wrong once is never offered again.

Usage:
    python storage/review_matches.py --status
    python storage/review_matches.py --emit              # writes review_queue.csv
    python storage/review_matches.py --emit --limit 30
    # fill in the verdict column with accept or reject, then:
    python storage/review_matches.py --apply review_queue.csv
    python storage/load_dol.py --rebuild-mapping-only    # to take effect
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect  # noqa: E402
from load_dol import ALIASES, load_aliases  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("review_matches")

DEFAULT_QUEUE = "review_queue.csv"

# What the reviewer sees. Evidence sits next to the decision rather than in a
# separate report, because a decision made without the numbers in front of it is
# the decision this whole mechanism exists to avoid.
FIELDS = ["company_name", "employer_key", "verdict", "postings", "intern_entry",
          "filings", "certified_pct", "states", "distinct_titles", "employer_name",
          "why", "evidence"]

# Pairs the matcher generated but does not trust, with everything needed to judge
# them. A pair already linked is settled; a pair already in the alias file was
# decided once and is never offered again.
QUEUE = """
    SELECT c.company_name,
           c.employer_key,
           coalesce(p.postings, 0)      AS postings,
           coalesce(p.intern_entry, 0)  AS intern_entry,
           coalesce(c.filings, 0)       AS filings,
           d.certified_pct,
           d.distinct_states,
           d.distinct_titles,
           d.employer_name,
           k.match_type
    FROM company_employer_candidates c
    LEFT JOIN (
        SELECT company_name,
               count(*)                                                    AS postings,
               count(*) FILTER (WHERE seniority IN ('intern', 'entry'))    AS intern_entry
        FROM stg_jobs GROUP BY 1
    ) p ON p.company_name = c.company_name
    LEFT JOIN company_employer_key k ON k.company_name = c.company_name
    LEFT JOIN LATERAL (
        SELECT employer_name, certified_pct, distinct_states, distinct_titles
        FROM dol_employer_summary s
        WHERE s.employer_key = c.employer_key
        ORDER BY fiscal_year DESC LIMIT 1
    ) d ON true
    WHERE NOT EXISTS (
        SELECT 1 FROM company_employer_link l
        WHERE l.company_name = c.company_name AND l.employer_key = c.employer_key
    )
    ORDER BY coalesce(p.postings, 0) DESC, c.company_name, c.employer_key
"""


def undecided(cur, decided: set[tuple[str, str]]) -> list[dict]:
    cur.execute(QUEUE)
    rows = []
    for (company, key, postings, intern_entry, filings, certified,
         states, titles, employer_name, match_type) in cur.fetchall():
        if (company, key) in decided:
            continue
        rows.append({
            "company_name": company,
            "employer_key": key,
            "verdict": "",
            "postings": postings,
            "intern_entry": intern_entry,
            "filings": filings,
            "certified_pct": "" if certified is None else f"{float(certified):.0f}",
            "states": states or "",
            "distinct_titles": titles or "",
            "employer_name": employer_name or "",
            "why": match_type or "no match",
            "evidence": "",
        })
    return rows


def decided_pairs() -> set[tuple[str, str]]:
    accepts, rejects = load_aliases()
    pairs = set(rejects)
    for company, keys in accepts.items():
        pairs |= {(company, k) for k in keys}
    return pairs


def emit(cur, path: str, limit: int | None) -> None:
    rows = undecided(cur, decided_pairs())
    if limit:
        rows = rows[:limit]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    postings = sum(r["postings"] for r in rows)
    log.info("Wrote %s undecided pair(s) to %s, covering %s postings",
             f"{len(rows):,}", path, f"{postings:,}")
    log.info("Fill in the verdict column with accept or reject, then: "
             "python storage/review_matches.py --apply %s", path)


def apply(cur, path: str) -> None:
    """Merge the worksheet's verdicts into the alias file."""
    if not os.path.exists(path):
        raise SystemExit(f"{path} does not exist - run --emit first")

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    decisions = [r for r in rows if (r.get("verdict") or "").strip().lower()
                 in ("accept", "reject")]
    if not decisions:
        log.warning("No verdicts filled in - nothing to apply.")
        return

    # A key that does not exist would silently remove evidence rather than add it,
    # which is the failure mode this file is supposed to prevent.
    cur.execute("SELECT DISTINCT employer_key FROM dol_employer_summary")
    known = {k for (k,) in cur.fetchall()}
    unknown = sorted({r["employer_key"] for r in decisions
                      if r["employer_key"] not in known})
    if unknown:
        raise SystemExit(f"these employer keys do not exist in the DOL data: {unknown}")

    existing: list[dict] = []
    if os.path.exists(ALIASES):
        with open(ALIASES, newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    seen = {(r["company_name"], r["employer_key"]) for r in existing}

    added = 0
    for r in decisions:
        pair = (r["company_name"], r["employer_key"])
        if pair in seen:
            continue
        note = (r.get("evidence") or "").strip()
        if not note:
            # The evidence column is what makes a decision reviewable a year
            # later, so one is written when the reviewer did not.
            note = (f"{r.get('filings') or 0} filings; {r.get('postings') or 0} "
                    f"postings; reviewed from the queue")
        existing.append({
            "company_name": r["company_name"],
            "employer_key": r["employer_key"],
            "verdict": r["verdict"].strip().lower(),
            "evidence": note,
        })
        seen.add(pair)
        added += 1

    existing.sort(key=lambda r: (r["company_name"].lower(), r["employer_key"]))
    with open(ALIASES, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["company_name", "employer_key",
                                                "verdict", "evidence"])
        writer.writeheader()
        writer.writerows(existing)

    log.info("Added %s decision(s) to %s; it now holds %s.",
             f"{added:,}", os.path.relpath(ALIASES), f"{len(existing):,}")
    log.info("Run python storage/load_dol.py --rebuild-mapping-only to apply them.")


def status(cur) -> None:
    rows = undecided(cur, decided_pairs())
    companies = {r["company_name"] for r in rows}
    log.info("%s undecided pair(s) across %s companies", f"{len(rows):,}",
             f"{len(companies):,}")
    log.info("  %s postings and %s intern/entry roles are affected",
             f"{sum(r['postings'] for r in rows):,}",
             f"{sum(r['intern_entry'] for r in rows):,}")
    top = rows[:10]
    if top:
        log.info("  the ten costing the most evidence:")
        for r in top:
            log.info("    %-32s -> %-38s %5s postings, %6s filings",
                     r["company_name"][:32], r["employer_key"][:38],
                     f"{r['postings']:,}", f"{r['filings']:,}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Review undecided employer matches")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="what is undecided")
    group.add_argument("--emit", action="store_true", help="write the worksheet")
    group.add_argument("--apply", metavar="PATH", nargs="?", const=DEFAULT_QUEUE,
                       help="merge a filled-in worksheet into the alias file")
    ap.add_argument("--out", default=DEFAULT_QUEUE, help="worksheet path for --emit")
    ap.add_argument("--limit", type=int, help="only the N most costly pairs")
    args = ap.parse_args()

    conn = connect(autocommit=True)
    try:
        with conn.cursor() as cur:
            if args.status:
                status(cur)
            elif args.emit:
                emit(cur, args.out, args.limit)
            else:
                apply(cur, args.apply)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
