"""
Draft the three outreach messages for a company.

No model call. Every number is interpolated from an insight record that came
out of SQL, so a message cannot contain a figure the warehouse cannot
reproduce. That matters more here than anywhere else in the project: these go
to people who know their own hiring data, and one invented number ends the
conversation and the credibility behind it.

Three variants, because the channels differ and a message written for one
reads badly in the other:

    connection   LinkedIn connection note, hard cap 300 characters
    followup     LinkedIn message after the request is accepted, ~120 words
    email        cold email, ~105 words

The structure is fixed and deliberate:

  1. Something true and specific about THEM, first - not about the sender.
  2. What was built, as one byline, with a link they can check in ten seconds.
  3. An ask for their opinion rather than their time. "Would you be open to a
     chat" asks a stranger for a calendar slot; "does this hold up" asks for
     something they can answer in one line, and answering it starts the
     conversation anyway.
  4. The role last, framed as interest rather than an application, and never
     with a requisition number - a req number turns a message into a ticket.

Usage:
    python outreach/compose.py --companies Databricks Stripe
    python outreach/compose.py --companies-file targets.txt --json drafts.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "storage"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_layer"))
sys.path.insert(0, os.path.dirname(__file__))

from candidate_profile import PROFILE  # noqa: E402
from db import connect  # noqa: E402
from insights import (  # noqa: E402
    _fetch_facts,
    build_insights,
    collection_days,
    load_market,
    peer_stats,
)

SITE_URL = os.getenv("SITE_URL", "https://zohaiba365.github.io/Signal")

CONNECTION_LIMIT = 300      # LinkedIn's hard cap on a connection note


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unknown"


def company_url(company: str) -> str:
    return f"{SITE_URL}/companies/{slugify(company)}/"


# Whether the sender is the person who built the dataset, or somebody using it.
#
# This is not a wording preference. Three of these templates say "a pipeline I
# built" and "I build a public dataset", which is true of the owner and a lie in
# anybody else's name. A stranger using the public page is citing the data, not
# claiming to have made it, and the sentences change accordingly. Getting this
# wrong would put a false claim in a message someone actually sends.
def _authored(me: dict) -> bool:
    return not me.get("_borrowed")


# The insight sentences are written in the owner's voice - "the companies I track"
# - because until now the owner was the only person sending them. In somebody
# else's message that phrase contradicts the sentence above it, which says they
# were reading a public dataset rather than keeping one. Swapped at the point of
# use rather than changed in insights.py, because the site and the owner's own
# drafts should keep saying "I track": there, it is true.
BORROWED_VOICE = (
    ("the companies I track", "the companies it tracks"),
    ("companies I track", "companies it tracks"),
    ("I've been accumulating", "that has been accumulating"),
)


def _voice(text: str, me: dict) -> str:
    if _authored(me):
        return text
    for mine, theirs in BORROWED_VOICE:
        text = text.replace(mine, theirs)
    return text


def _article(phrase: str) -> str:
    """"an Ai Engineer", "a CS student". Crude, and right far more often than not."""
    return "an" if phrase[:1].lower() in "aeiou" else "a"


# Nouns that need an article in front of them when somebody types a bare one.
# "Looking for Summer 2027" is a sentence; "looking for Summer 2027 internship" is
# not. Deliberately a short list of concrete nouns rather than anything clever:
# a wrong guess here puts a wrong word in a message a person sends, so the rule
# only fires where the answer is unambiguous, and leaves everything else alone.
# Singular only: a plural ("internships") is already a complete noun phrase and
# "an internships" is the kind of mistake this function exists to avoid.
COUNTABLE = ("internship", "co-op", "coop", "role", "job", "position",
             "placement", "term", "gig", "traineeship")


def _needs_article(phrase: str) -> str:
    # Determiners only. "full-time" is an adjective and still wants an article in
    # front of it - "looking for full-time data role" was the bug that made this
    # list explicit rather than a guess at what a leading word might be.
    first = phrase.split(" ", 1)[0].lower()
    if first in ("a", "an", "the", "my", "any", "some", "another", "one"):
        return phrase
    if phrase.rstrip(".").split(" ")[-1].lower() in COUNTABLE:
        return f"{_article(phrase)} {phrase}"
    return phrase


def _about(me: dict) -> str:
    """"I'm a Computer Science student at McGill", from whatever was given."""
    program, school = me.get("program", "").strip(), me.get("school", "").strip()

    # A visitor's words are used exactly as typed, because the page asks open
    # questions - "What you do", "Where" - and the answers already contain their
    # own nouns. Wrapping them in "student" regardless turned "Ai Engineer" into
    # "I'm a Ai Engineer student at Uwaterloo": the wrong article, a job title
    # recast as a degree, and a description of somebody the sender is not. The
    # owner's profile is structured and genuinely is a student, so that branch is
    # unchanged and still reads as a sentence rather than a slot fill.
    if not _authored(me):
        if program and school:
            return f"I'm {_article(program)} {program} at {school},"
        if program:
            return f"I'm {_article(program)} {program},"
        if school:
            return f"I'm at {school},"
        return ""

    if program and school:
        return f"I'm a {program} student at {school},"
    if program:
        return f"I'm a {program} student,"
    if school:
        return f"I'm a student at {school},"
    return "I'm a student,"


def _wants(me: dict) -> str:
    """
    "looking for a Winter 2027 Data Engineer term", or the visitor's own phrase.

    Same reasoning as _about. The owner seeks a co-op term and the word is
    accurate; a visitor typed whatever they are looking for into a box labelled
    "Looking for", and "looking for a full-time data role term" is not a sentence.
    """
    if not _authored(me):
        want = me.get("term", "").strip()
        return f"looking for {_needs_article(want)}" if want else ""
    seeking = _seeking(me)
    return f"looking for a {seeking} term" if seeking else ""


def _intro(me: dict) -> str:
    """Who the sender is and what they want, as one sentence, or nothing at all."""
    about, wants = _about(me), _wants(me)
    if about and wants:
        return f"{about} {wants}."
    if about:
        return about.rstrip(",") + "."
    if wants:
        return f"I'm {wants}."
    return ""


def _seeking(me: dict) -> str:
    """
    "Winter 2027 Data Engineer" from whichever parts were given.

    Every field is optional on the public page - somebody may give a term and no
    role, or neither - and the templates used to index them directly, so a missing
    one raised a KeyError in the middle of writing a message. Joined rather than
    interpolated, so an absent part leaves no gap behind it.
    """
    return " ".join(x for x in (me.get("term", ""), me.get("role", "")) if x)


def _sender() -> dict:
    """
    The few profile fields the templates need, in message-ready form.

    Every message function now takes an optional `sender` and falls back to this.
    The public page passes the visitor's own details so the draft is theirs to
    send; everything run from the command line passes nothing and gets the
    configured profile, exactly as before.
    """
    p = PROFILE
    seeking = p["seeking"]
    return {
        "school": p.get("school", ""),
        "program": p.get("program", ""),
        "term": seeking.get("term", ""),
        "months": seeking.get("months", 4),
        "role": p["target_roles"][0],
    }


def possessive(name: str) -> str:
    """Databricks' rather than Databricks's."""
    return f"{name}'" if name.rstrip().endswith("s") else f"{name}'s"


def _para(*sentences: str) -> str:
    """One paragraph as one line. Wrapping is a display concern, not stored."""
    return " ".join(" ".join(x.split()) for x in sentences if x)


def _lead(insights: list) -> tuple[str, str | None]:
    """
    The strongest fact, and a second one that is genuinely different.

    Raw volume is true but the least surprising thing to open with, so a peer
    or market comparison leads when one exists. Those are also the figures
    nothing else can produce, which is the only reason a stranger reads past
    the first line.
    """
    if not insights:
        return "", None
    distinctive = [i for i in insights if i.tier != "company"]
    ordered = distinctive + [i for i in insights if i not in distinctive]
    lead = ordered[0]
    second = next((i.text for i in ordered[1:]
                   if i.tier != lead.tier and i.kind != lead.kind), None)
    return lead.text, second


def connection_note(company: str, insights: list, sender: dict | None = None) -> str:
    """LinkedIn connection note. Hard 300-character cap, so one fact only."""
    lead, _ = _lead(insights)
    me = sender or _sender()
    lead = _voice(lead, me)
    source = ("I build a public dataset on data-engineering hiring and"
              if _authored(me) else
              "I was reading a public dataset on data-engineering hiring and")
    where = f" The numbers are at {company_url(company)}." if _authored(me) else ""
    note = (f"Hi - {source} "
            f"{company} came up: {lead}.{where} {_intro(me).rstrip('.')}"
            f"{' - ' if _intro(me) else ''}would value your read on it.")
    if len(note) > CONNECTION_LIMIT:
        # Drop the sender's own description before truncating anything factual:
        # the fact is what earns the accept, and for the owner so is the link.
        short = f" Numbers here: {company_url(company)}" if _authored(me) else ""
        note = (f"Hi - {source} "
                f"{company} came up: {lead}.{short} - would value your read.")
    if len(note) > CONNECTION_LIMIT:
        note = note[:CONNECTION_LIMIT - 1].rsplit(" ", 1)[0] + "…"
    return note


def followup(company: str, insights: list, sender: dict | None = None) -> str:
    """Sent after a connection request is accepted. ~120 words."""
    lead, second = _lead(insights)
    me = sender or _sender()
    lead = _voice(lead, me)
    second = _voice(second, me) if second else second
    # `second` is a clause about the company ("220 of their 997 open roles
    # were posted in the last 30 days"), so it needs a connector that takes a
    # clause. "They also showed up as <clause>" does not parse.
    also = f" On top of that, {second}." if second else ""
    return "\n\n".join([
        "Thanks for connecting.",
        _para(f"The reason {company} caught my attention: {lead}.{also}",
              *(("That comes out of a pipeline I built that tracks",
                 "data-engineering hiring daily - postings from company career",
                 "boards, visa filings from the Department of Labor, and a",
                 "per-technology demand index I've been accumulating because",
                 "nobody publishes one.")
                if _authored(me) else
                ("That comes out of a public dataset tracking data-engineering",
                 "hiring daily - postings from company career boards, visa filings",
                 "from the Department of Labor, and a per-technology demand",
                 "index.")),
              *((f"Your page is at {company_url(company)} if you want to check the",
                 "figures.") if _authored(me) else ())),
        _para(_intro(me) or "Getting in touch because it seemed worth asking.",
              "Not asking you to forward a resume - I'd genuinely value your read",
              f"on whether this holds up against how {company} actually works."),
    ])


def email(company: str, insights: list, sender: dict | None = None) -> str:
    """Cold email. ~105 words, and the subject line carries the fact."""
    lead, _ = _lead(insights)
    me = sender or _sender()
    lead = _voice(lead, me)
    subject = f"{possessive(company)} hiring, from the data side"
    body = "\n\n".join([
        "Hi,",
        _para(("I maintain a public dataset on data-engineering hiring, and"
               if _authored(me) else
               "I was looking through a public dataset on data-engineering hiring,"
               " and"),
              # The link is the owner's, and only the owner sends it. A visitor
              # citing a public dataset has no business pointing a stranger at
              # somebody else's project as though it were their own credential -
              # and a recipient who follows it lands on a page that belongs to a
              # third person the message never mentions.
              (f"{company} stood out: {lead}. The page is {company_url(company)}"
               if _authored(me) else f"{company} stood out: {lead}."),
              *(["- every figure there traces back to a query."]
                if _authored(me) else [])),
        *([_para("I built it end to end: daily ingestion from company career boards",
                 "into a warehouse, dbt models, and a per-technology demand index",
                 "accumulated daily because no public source has one.")]
          if _authored(me) else []),
        _para(_intro(me) or "Getting in touch because it seemed worth asking.",
              "Rather than a resume, I'd value your opinion: does this match how",
              f"hiring actually looks from inside {company}?"),
        *([me["name"]] if me.get("name") else []),
    ])
    return f"Subject: {subject}\n\n{body}"


def baseline_companies(cur, names: list[str]) -> list[str]:
    """Every company with enough postings to be a fair comparison, plus the targets."""
    cur.execute("""
        SELECT company_name FROM dim_company
        WHERE total_postings >= 20 AND company_name IS NOT NULL
    """)
    rows = [r[0] if not isinstance(r, dict) else list(r.values())[0]
            for r in cur.fetchall()]
    return sorted(set(rows) | set(names))


def drafts_for(cur, company: str, peers: dict, market: dict, days: int,
               sender: dict | None = None) -> dict:
    facts = _fetch_facts(cur, company)
    if not facts or not facts.get("roles"):
        return {"company": company, "error": "no postings stored"}
    insights = build_insights(company, facts, peers, market, days)
    if not insights:
        return {"company": company, "error": "no usable insight"}
    return {
        "company": company,
        "url": company_url(company),
        "insights": [{"tier": i.tier, "kind": i.kind, "text": i.text,
                      "evidence": i.evidence} for i in insights],
        "connection": connection_note(company, insights, sender),
        "followup": followup(company, insights, sender),
        "email": email(company, insights, sender),
    }


def _wrapped(text: str, width: int = 76) -> str:
    """Wrap for the terminal only. Stored messages keep whole paragraphs."""
    return "\n\n".join(textwrap.fill(x, width) for x in text.split("\n\n"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Draft outreach messages")
    ap.add_argument("--companies", nargs="+")
    ap.add_argument("--companies-file")
    ap.add_argument("--json", help="write all drafts here")
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
    # Peer baselines come from the whole corpus, not just the companies asked
    # for. peer_stats derives its comparison set from the list it is handed, so
    # drafting for a single company produced no peer tier at all - and the peer
    # comparison is the strongest thing in the message. Databricks led with
    # "220 of 997 roles posted recently" instead of "Apache Spark in 17% of
    # their postings, 3.1x comparable companies", which is the line that earns
    # a reply.
    peers = peer_stats(cur, baseline_companies(cur, names))
    days = collection_days(cur)

    out = []
    for name in names:
        d = drafts_for(cur, name, peers, market, days)
        out.append(d)
        print("=" * 78)
        if d.get("error"):
            print(f"{name}: {d['error']}")
            continue
        print(f"{name}   {d['url']}")
        print(f"\n-- LinkedIn connection note ({len(d['connection'])} chars) --")
        print(textwrap.fill(d["connection"], 76))
        print(f"\n-- LinkedIn follow-up ({len(d['followup'].split())} words) --")
        print(_wrapped(d["followup"]))
        print(f"\n-- Email ({len(d['email'].split())} words) --")
        print(_wrapped(d["email"]))
        print()

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"Wrote {len(out)} drafts to {args.json}")
    conn.close()


if __name__ == "__main__":
    main()
