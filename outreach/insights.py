"""
Per-company observations for cold outreach.

Produces the opening line of a message: a specific, true, checkable fact about
a company's hiring. Everything here is computed in SQL from stored postings -
no model call - so the same company always yields the same numbers and any
figure can be re-derived before it is sent.

Three tiers, in increasing order of how hard they are to get anywhere else:

    company   "They have 110 open roles, 63 posted in the last 30 days."
    peer      "That is roughly 3x the pace of comparable companies."
    market    "They hire for Spark; demand for it is falling market-wide."

Tier 3 is the reason the market observatory exists. Anyone can count a
company's job postings. Placing that company against the market it hires in
requires the daily index.

Rules the generator follows, learned the hard way:

  * State counts, shares and comparisons - never judgements about the
    recipient's performance. "Your reqs average 41 days open" was tested and
    dropped: the market median is 30 days and the mean 60, so 41 is
    unremarkable, and posting age has confounds (some employers never take
    listings down).
  * Every insight carries its evidence, so nothing goes into a message that
    cannot be traced back to a query.

Usage:
    python outreach/insights.py --companies Stripe Ramp Datadog
    python outreach/insights.py --companies-file targets.txt --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
from db import connect  # noqa: E402

# Technologies so common that naming them says nothing about a company.
# "They hire for Python" is true of essentially every employer in the index
# and reads as filler, which is worse than saying nothing.
UBIQUITOUS = {
    "python", "sql", "java", "aws", "azure", "gcp", "git", "docker",
    "machine_learning", "bash", "typescript",
}


@dataclass
class Insight:
    tier: str                       # company | peer | market
    kind: str
    text: str                       # the sentence, ready to drop into a message
    evidence: dict = field(default_factory=dict)
    strength: int = 0               # higher ranks first


FACTS_SQL = """
WITH p AS (
    SELECT * FROM raw_postings WHERE company_name = %(company)s
)
SELECT
    (SELECT count(*) FROM p)                                                   AS roles,
    (SELECT count(*) FROM p WHERE posted_date > now() - interval '30 days')    AS last_30d,
    (SELECT count(*) FROM p WHERE posted_date > now() - interval '60 days'
                              AND posted_date <= now() - interval '30 days')   AS prior_30d,
    (SELECT count(DISTINCT location_state) FROM p WHERE location_state IS NOT NULL) AS states,
    (SELECT count(DISTINCT category) FROM p WHERE category IS NOT NULL)        AS departments,
    (SELECT string_agg(DISTINCT country, ',') FROM p)                          AS countries
"""

TOP_DEPTS_SQL = """
SELECT category, count(*) n
FROM raw_postings
WHERE company_name = %(company)s AND category IS NOT NULL
GROUP BY 1 ORDER BY n DESC LIMIT 3
"""

STACK_SQL = """
SELECT pt.tech_slug, count(*) n
FROM raw_postings r
JOIN posting_technologies pt ON pt.source = r.source AND pt.job_id = r.job_id
WHERE r.company_name = %(company)s
GROUP BY 1 ORDER BY n DESC
"""

# Market-wide demand for a technology, from the daily snapshot.
MARKET_SQL = """
SELECT tech_slug, tech_name, openings,
       rank() OVER (PARTITION BY category ORDER BY openings DESC) AS rank_in_category,
       category
FROM market_demand
WHERE snapshot_date = (SELECT max(snapshot_date) FROM market_demand)
"""


def _fetch_facts(cur, company: str) -> dict:
    cur.execute(FACTS_SQL, {"company": company})
    cols = [c.name for c in cur.description]
    facts = dict(zip(cols, cur.fetchone(), strict=True))
    cur.execute(TOP_DEPTS_SQL, {"company": company})
    facts["top_departments"] = cur.fetchall()
    cur.execute(STACK_SQL, {"company": company})
    facts["stack"] = cur.fetchall()
    return facts


def _pct(a: float, b: float) -> float:
    return round(100.0 * (a - b) / b, 0) if b else 0.0


def _is_own_product(company: str, tech_slug: str, tech_name: str) -> bool:
    """
    True when the technology IS the company's product.

    Without this the generator produced "Databricks stood out: they hire for
    Databricks, the most in-demand warehouse technology" - which tells the
    recipient something about their own product and destroys the credibility
    of everything after it.
    """
    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t.lower())

    c, ts, tn = norm(company), norm(tech_slug), norm(tech_name)
    return c and (c in ts or ts in c or c in tn or tn in c)


def build_insights(company: str, facts: dict, peers: dict, market: dict) -> list[Insight]:
    out: list[Insight] = []
    roles, last30, prior30 = facts["roles"], facts["last_30d"], facts["prior_30d"]

    # ---- tier 1: about them ------------------------------------------------
    if roles >= 5 and last30 >= 3:
        share = round(100.0 * last30 / roles)
        out.append(Insight(
            "company", "recent_volume",
            f"{last30} of their {roles} open roles were posted in the last 30 days",
            {"last_30d": last30, "roles": roles, "share_pct": share},
            strength=60 + min(share, 40),
        ))

    if prior30 >= 3 and last30 >= 3:
        change = _pct(last30, prior30)
        if abs(change) >= 40:
            direction = "up" if change > 0 else "down"
            out.append(Insight(
                "company", "pace_change",
                f"their posting pace is {direction} {abs(int(change))}% versus the previous month",
                {"last_30d": last30, "prior_30d": prior30, "pct_change": change},
                strength=70 + min(int(abs(change)) // 10, 25),
            ))

    if facts["states"] >= 5:
        out.append(Insight(
            "company", "geography",
            f"they are hiring across {facts['states']} states",
            {"states": facts["states"]}, strength=40 + min(facts["states"], 20),
        ))

    if facts["top_departments"]:
        dept, n = facts["top_departments"][0]
        if n >= 5:
            out.append(Insight(
                "company", "team_focus",
                f"their largest open team is {dept} with {n} roles",
                {"department": dept, "roles": n}, strength=45,
            ))

    # ---- tier 2: against comparable companies ------------------------------
    if peers.get("median_last_30d") and last30 >= 3:
        ratio = last30 / peers["median_last_30d"]
        if ratio >= 1.8 or ratio <= 0.55:
            comparison = f"{ratio:.1f}x" if ratio >= 1 else f"{1/ratio:.1f}x below"
            out.append(Insight(
                "peer", "pace_vs_peers",
                f"that is roughly {comparison} the pace of comparable companies I track",
                {"company_last_30d": last30, "peer_median": peers["median_last_30d"]},
                strength=80,
            ))

    # Deliberately NOT generating "comparable companies hire for X and they do
    # not". A technology missing from job postings is not evidence it is
    # missing from the stack - Stripe almost certainly runs dbt-shaped tooling
    # whether or not a req names it. Telling a stranger they lack something
    # they actually have is the single most damaging error available here, so
    # the comparison is only made in the positive direction, where the
    # evidence is a count that exists rather than one that does not.
    if peers.get("stack_shares") and facts["roles"] >= 20:
        for slug, n in facts["stack"][:12]:
            if slug in UBIQUITOUS or n < 3:
                continue
            own = n / facts["roles"]
            peer = peers["stack_shares"].get(slug, 0.0)
            nm = market.get(slug, {}).get("name", slug)
            if _is_own_product(company, slug, nm):
                continue
            if peer and own >= peer * 2.0 and own >= 0.03:
                out.append(Insight(
                    "peer", "stack_emphasis",
                    f"they mention {nm} in {round(own * 100)}% of their postings, "
                    f"about {own / peer:.1f}x the rate of comparable companies I track",
                    {"tech": slug, "own_share": round(own, 3), "peer_share": round(peer, 3)},
                    strength=88,
                ))
                break

    # ---- tier 3: against the market ----------------------------------------
    for slug, n in facts["stack"][:8]:
        m = market.get(slug)
        if slug in UBIQUITOUS or not m or _is_own_product(company, slug, m["name"]):
            continue
        if m["rank_in_category"] <= 2 and n >= 3:
            out.append(Insight(
                "market", "leading_tech",
                f"they hire for {m['name']}, currently the most in-demand "
                f"{m['category']} technology in the US market",
                {"tech": slug, "openings_market_wide": m["openings"]},
                strength=72,
            ))
            break

    out.sort(key=lambda i: -i.strength)
    return out


def peer_stats(cur, companies: list[str]) -> dict:
    """Baselines from the target set itself - no external data needed."""
    stats = {"n": len(companies)}
    cur.execute("""
        SELECT count(*) FILTER (WHERE posted_date > now() - interval '30 days')
        FROM raw_postings WHERE company_name = ANY(%(cos)s) GROUP BY company_name
    """, {"cos": companies})
    counts = sorted(r[0] for r in cur.fetchall() if r[0])
    if counts:
        stats["median_last_30d"] = counts[len(counts) // 2]

    # Share of the peer set's postings mentioning each technology, which is
    # what makes "2x the rate of comparable companies" a checkable claim.
    cur.execute("""
        WITH peer AS (
            SELECT r.source, r.job_id FROM raw_postings r
            WHERE r.company_name = ANY(%(cos)s)
        ), total AS (SELECT count(*)::numeric n FROM peer)
        SELECT pt.tech_slug, count(*)::numeric / (SELECT n FROM total)
        FROM peer p JOIN posting_technologies pt
          ON pt.source = p.source AND pt.job_id = p.job_id
        GROUP BY 1
    """, {"cos": companies})
    stats["stack_shares"] = {s: float(share) for s, share in cur.fetchall()}
    return stats


def load_market(cur) -> dict:
    cur.execute(MARKET_SQL)
    return {r[0]: {"name": r[1], "openings": r[2], "rank_in_category": r[3], "category": r[4]}
            for r in cur.fetchall()}


def opening_line(company: str, insights: list[Insight]) -> str:
    """The first sentence of the message: the two strongest facts."""
    if not insights:
        return f"(no usable insight for {company} - too few postings stored)"
    # Prefer a distinctive observation (pace change, peer or market
    # comparison) as the lead; raw volume is true but the least surprising
    # thing you can open with.
    distinctive = [i for i in insights if i.tier != "company" or i.kind == "pace_change"]
    ordered = distinctive + [i for i in insights if i not in distinctive]
    lead = ordered[0]
    line = f"{company} stood out in the data: {lead.text}"
    for nxt in ordered[1:]:
        if nxt.kind != lead.kind and nxt.tier != lead.tier:
            line += f", and {nxt.text}"
            break
    return line + "."


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate outreach insights per company")
    ap.add_argument("--companies", nargs="+")
    ap.add_argument("--companies-file")
    ap.add_argument("--json", help="write full insight records here")
    args = ap.parse_args()

    names = list(args.companies or [])
    if args.companies_file:
        with open(args.companies_file) as fh:
            names += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not names:
        raise SystemExit("Give --companies or --companies-file")

    conn = connect(autocommit=True)
    cur = conn.cursor()
    market = load_market(cur)
    peers = peer_stats(cur, names)

    results = []
    for name in names:
        facts = _fetch_facts(cur, name)
        if not facts["roles"]:
            print(f"\n{'=' * 78}\n{name}\n  no postings stored - run ingestion/company_boards.py first")
            continue
        ins = build_insights(name, facts, peers, market)
        results.append({"company": name, "facts": {k: v for k, v in facts.items()
                                                   if k not in ("stack", "top_departments")},
                        "insights": [asdict(i) for i in ins],
                        "opening_line": opening_line(name, ins)})
        print(f"\n{'=' * 78}\n{name}  ({facts['roles']} roles, {facts['last_30d']} in last 30d)")
        print(f"\n  OPENING LINE:\n    {opening_line(name, ins)}\n")
        if ins:
            print("  all insights, strongest first:")
            for i in ins:
                print(f"    [{i.tier:<7}] {i.text}")

    conn.close()
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(results, fh, indent=2, default=str)
        print(f"\nWrote {len(results)} records to {args.json}")


if __name__ == "__main__":
    main()
