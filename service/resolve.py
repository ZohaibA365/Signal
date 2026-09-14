"""
Turn a pasted job posting into a company the agent can actually say something about.

The drafts are built from what the warehouse knows about an employer, so a company
Signal has never collected postings for has nothing true to say about it. That is a
property of the design rather than a gap: the whole reason the drafts are
defensible is that every figure in them came out of a query.

So the paste is used to identify the employer, and the run then executes against a
real open posting there. When the employer is not tracked, this says so and offers
the nearest ones instead - which is a better outcome than a fabricated message, and
is itself the system refusing to invent something.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("storage", "ingestion"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from dol_ingest import normalise_employer  # noqa: E402

# Lines a posting puts a company name on. Ordered by how reliable each is.
LABELLED = re.compile(
    r"^\s*(?:company|employer|organisation|organization|at)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE)
# The keyword is case-insensitive; the company name is not. An IGNORECASE flag on
# the whole pattern also makes [A-Z] match lowercase, which turned "Join Stripe as a
# data engineering intern" into the candidate "Stripe as a data" - so the flag is
# scoped to the group that needs it.
AT_PHRASE = re.compile(r"\b(?i:at|join|with)\s+([A-Z][\w&.,'-]*(?:\s+[A-Z][\w&.,'-]*){0,3})")

# Words that turn a capitalised phrase into something that is not a company.
NOT_A_COMPANY = {"we", "our", "the", "this", "you", "your", "us", "job", "role",
                 "apply", "posted", "location", "remote", "hybrid", "full", "part"}


def candidates(text: str) -> list[str]:
    """Everything in the pasted text that might be an employer name, best first."""
    found: list[str] = []

    for match in LABELLED.findall(text or ""):
        cleaned = match.strip().split("\n")[0].strip(" .,-")
        if cleaned:
            found.append(cleaned)

    # The first line of a posting is very often the title, and the second the
    # company. Both are cheap to try and neither is trusted without a database hit.
    # Sentences are excluded: "Join Stripe as a data engineering intern this winter"
    # is under sixty characters and is not an employer, and letting it through meant
    # the failure message named it instead of naming Stripe.
    for line in (text or "").splitlines()[:4]:
        line = line.strip(" .,-|")
        if not (2 <= len(line) <= 60) or line.lower() in NOT_A_COMPANY:
            continue
        if len(line.split()) > 5 or line.endswith("."):
            continue
        found.append(line)

    for match in AT_PHRASE.findall(text or ""):
        cleaned = match.strip(" .,-")
        if cleaned and cleaned.split()[0].lower() not in NOT_A_COMPANY:
            found.append(cleaned)

    seen, ordered = set(), []
    for name in found:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(name)
    return ordered[:12]


def tracked(cur) -> dict[str, str]:
    """
    Every tracked employer, keyed by its normalised name.

    Matched in Python rather than in SQL on purpose. The keys have to be produced
    by the SAME function on both sides, and normalise_employer strips legal
    suffixes as well as punctuation - a SQL approximation of it missed "Stripe"
    because the warehouse holds "Stripe, Inc.". Three and a half thousand names is
    nothing to fetch, and being exactly right matters more here than being clever.
    """
    cur.execute("""
        SELECT company_name FROM dim_company
        WHERE company_name IS NOT NULL AND total_postings >= 5
    """)
    out = {}
    for (name,) in cur.fetchall():
        key = normalise_employer(name)
        # First writer wins, and the query is ordered by nothing in particular, so
        # prefer the shorter name when two normalise alike - "Oracle" over
        # "Oracle Corporation".
        if key and (key not in out or len(name) < len(out[key])):
            out[key] = name
    return out


def resolve(cur, text: str) -> dict:
    """
    Find the tracked employer this posting is about.

    Matching goes through normalise_employer, the same function the sponsorship
    mapping uses, so "Stripe" reaches "Stripe, Inc." and the behaviour here agrees
    with the rest of the project.
    """
    names = candidates(text)
    if not names:
        return {"found": False, "reason": "No company name found in that text.",
                "suggestions": suggestions(cur)}

    known = tracked(cur)
    for name in names:
        key = normalise_employer(name)
        if key and key in known:
            return {"found": True, "company": known[key], "matched_from": name}

    # Deliberately does not name a candidate. Picking the shortest one announced
    # "Signal has no postings for Software Engineer" on a paste whose employer was
    # Acme Robotics - confidently naming the wrong thing, which reads worse than
    # naming nothing. If the parser were sure, the lookup would have succeeded.
    return {"found": False, "tried": names[:3],
            "reason": "Signal has not collected postings for that employer, so there "
                      "is nothing true to say about them.",
            "suggestions": suggestions(cur)}


def suggestions(cur, n: int = 4) -> list[str]:
    """
    Tracked employers worth trying instead.

    Required to have an open role the agent can actually run against, not merely a
    lot of history. The first version suggested SpaceX and Anduril - the two largest
    employers in the corpus and, being cleared-work defence firms, the two least
    useful things to offer someone who needs sponsorship.
    """
    # Three conditions, and the third is the one that took two tries to get right.
    # Ranking by open intern roles offered Alo Yoga and Alliance Animal Health;
    # ranking by size then offered SpaceX and Anduril, the two largest employers in
    # the corpus and, being cleared-work defence firms, the two that sponsor nobody.
    # A suggestion here is an employer worth approaching, which in this project's
    # terms means one with sponsorship evidence behind it.
    cur.execute("""
        SELECT c.company_name
        FROM dim_company c
        JOIN int_company_sponsorship s
          ON s.company_name = c.company_name AND s.is_confident_match
        WHERE c.total_postings >= 30
          AND EXISTS (SELECT 1 FROM apply_queue a
                       WHERE a.company_name = c.company_name
                         AND a.link_tier = 'direct')
        ORDER BY c.total_postings DESC
        LIMIT %s
    """, (n,))
    return [r[0] for r in cur.fetchall()]


def posting_for(cur, company: str) -> dict | None:
    """
    The role the run will execute against: the best-scoring open one there.

    Real rather than synthetic, because every rail downstream checks against the
    warehouse - a fabricated posting would be rejected by the agent's own
    hallucination check, which is the correct behaviour and the wrong demonstration.
    """
    cur.execute("""
        SELECT a.source || ':' || a.job_id, a.company_name, a.job_title, a.fit_score
        FROM apply_queue a
        WHERE a.company_name = %s AND a.link_tier = 'direct'
        ORDER BY a.fit_score DESC NULLS LAST, a.job_id
        LIMIT 1
    """, (company,))
    row = cur.fetchone()
    if not row:
        return None
    return {"job_id": row[0], "company": row[1], "title": row[2], "fit_score": row[3]}
