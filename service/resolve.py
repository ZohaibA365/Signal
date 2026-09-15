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


# A pasted link. The common case, and the one the parser used to fail on outright:
# a URL has no capitalised company name and no "Company:" line, so every rule below
# found nothing and the page said "No company name found in that text" about a
# perfectly good job posting.
URL = re.compile(r"https?://[^\s<>\"\')]+")

# Where the employer sits in an applicant-tracking URL. These are the boards this
# project already ingests from, so the slug in the link is the same identifier the
# warehouse collected under.
#
#   boards.greenhouse.io/spacex/jobs/123        -> spacex
#   job-boards.greenhouse.io/fanatics/jobs/1    -> fanatics
#   jobs.lever.co/acme/uuid                     -> acme
#   jobs.ashbyhq.com/socure/uuid                -> socure
#   oracle.wd1.myworkdayjobs.com/en-US/Careers  -> oracle
#   klaviyo.com/careers/jobs/123                -> klaviyo
ATS_PATH = re.compile(
    r"https?://(?:www\.)?(?:job-)?boards\.greenhouse\.io/([\w.-]+)"
    r"|https?://(?:www\.)?jobs\.lever\.co/([\w.-]+)"
    r"|https?://(?:www\.)?jobs\.ashbyhq\.com/([\w.-]+)"
    r"|https?://(?:www\.)?apply\.workable\.com/([\w.-]+)"
    r"|https?://(?:www\.)?([\w-]+)\.wd\d+\.myworkdayjobs\.com",
    re.IGNORECASE)

# Hosts that identify a job board rather than an employer. The domain of one of
# these says nothing about who is hiring.
BOARD_HOSTS = {
    "linkedin", "indeed", "glassdoor", "ziprecruiter", "monster", "dice",
    "greenhouse", "lever", "ashbyhq", "workable", "myworkdayjobs", "workday",
    "smartrecruiters", "jobvite", "icims", "taleo", "bamboohr", "adzuna",
    "simplyhired", "google", "builtin", "wellfound", "angel", "handshake",
}


def urls(text: str) -> list[str]:
    return URL.findall(text or "")


def from_url(url: str) -> list[str]:
    """Employer names a link implies, best first."""
    out = []
    match = ATS_PATH.search(url)
    if match:
        slug = next((g for g in match.groups() if g), "")
        if slug and slug.lower() not in BOARD_HOSTS:
            # "andurilindustries" and "capital-one" are both the company written
            # without its spaces; the hyphen becomes one so the words survive for
            # the matcher below.
            out.append(slug.replace("-", " ").replace("_", " "))

    # The employer's own careers page: klaviyo.com/careers/... Taken only when NO
    # part of the host names a board. Testing the first label alone read
    # "boards.greenhouse.io" as a company called "boards", and "jobs.lever.co" as
    # one called "jobs" - both of which then went to the database as real guesses.
    host = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0].lower()
    parts = host.split(".")
    if len(parts) > 1 and not any(p in BOARD_HOSTS for p in parts):
        out.append(parts[0])

    seen, ordered = set(), []
    for name in out:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def posting_by_url(cur, url: str) -> str | None:
    """
    The employer of the exact posting this link points at.

    The strongest possible answer and the cheapest: the warehouse stores the
    redirect_url it collected, so a pasted link can be looked up rather than parsed.
    Compared without the query string, because the same posting is handed out with
    and without tracking parameters - greenhouse appends ?gh_jid= to its own links.
    """
    stripped = url.split("?")[0].split("#")[0].rstrip("/")
    cur.execute("""
        SELECT company_name FROM raw_postings
        WHERE company_name IS NOT NULL
          AND split_part(split_part(redirect_url, '?', 1), '#', 1) = %s
        LIMIT 1
    """, (stripped,))
    row = cur.fetchone()
    return row[0] if row else None


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
        # A link is short and has no spaces, so it satisfied every rule below and
        # was offered to the database as a company name. Links are read by
        # from_url(), which knows where an employer sits in one.
        if URL.match(line):
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
    links = urls(text)

    # A link that points at a posting the warehouse already holds answers the
    # question outright - no parsing, no guessing at which capitalised phrase is
    # the employer.
    for link in links:
        company = posting_by_url(cur, link)
        if company:
            return {"found": True, "company": company, "matched_from": link}

    # Otherwise the link still names the employer, in the ATS slug or the host.
    names = [n for link in links for n in from_url(link)] + candidates(text)
    if not names:
        # Two different failures, and telling them apart is the difference between
        # a useful sentence and a wrong one. A LinkedIn or Indeed URL is an opaque
        # id that names nobody; saying "not collected" there would blame the
        # dataset for a link that never identified an employer in the first place.
        reason = ("That link does not say who the employer is - LinkedIn and "
                  "Indeed links carry only a job number. Paste the company name, "
                  "or the posting itself." if links else
                  "That does not look like a job posting or a company name. Paste "
                  "the posting, its link, or just the company.")
        return {"found": False, "reason": reason, "suggestions": suggestions(cur)}

    known = tracked(cur)
    # A slug has no spaces and a company name does, so "andurilindustries" never
    # reached "Anduril Industries" on an exact key match - which made every
    # greenhouse link for a multi-word employer look untracked. Compared with the
    # spaces removed as well, which is unambiguous in both directions: no two
    # tracked employers differ only by where their spaces fall.
    squashed = {}
    for key, name in known.items():
        squashed.setdefault(key.replace(" ", ""), name)

    for name in names:
        key = normalise_employer(name)
        if not key:
            continue
        if key in known:
            return {"found": True, "company": known[key], "matched_from": name}
        flat = key.replace(" ", "")
        if flat in squashed:
            return {"found": True, "company": squashed[flat], "matched_from": name}

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
