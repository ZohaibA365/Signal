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
    """"I'm a Computer Science student at McGill", from the configured profile."""
    program, school = me.get("program", "").strip(), me.get("school", "").strip()
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

    The profile is structured - a term and a target role - so this reads as a
    sentence rather than a slot fill. A visitor's free text is handled in
    visitor.py, where the words they typed are used exactly as typed.
    """
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


def connection_note(company: str, insights: list) -> str:
    """LinkedIn connection note. Hard 300-character cap, so one fact only."""
    lead, _ = _lead(insights)
    me = _sender()
    note = (f"Hi - I build a public dataset on data-engineering hiring and "
            f"{company} came up: {lead}. The numbers are at "
            f"{company_url(company)}. {_intro(me).rstrip('.')}"
            f"{' - ' if _intro(me) else ''}would value your read on it.")
    if len(note) > CONNECTION_LIMIT:
        # Drop the sender's own description before truncating anything factual:
        # the fact and the link are what earn the accept.
        note = (f"Hi - I build a public dataset on data-engineering hiring and "
                f"{company} came up: {lead}. Numbers here: "
                f"{company_url(company)} - would value your read.")
    if len(note) > CONNECTION_LIMIT:
        note = note[:CONNECTION_LIMIT - 1].rsplit(" ", 1)[0] + "…"
    return note


def followup(company: str, insights: list) -> str:
    """Sent after a connection request is accepted. ~120 words."""
    lead, second = _lead(insights)
    me = _sender()
    # `second` is a clause about the company ("220 of their 997 open roles
    # were posted in the last 30 days"), so it needs a connector that takes a
    # clause. "They also showed up as <clause>" does not parse.
    also = f" On top of that, {second}." if second else ""
    return "\n\n".join([
        "Thanks for connecting.",
        _para(f"The reason {company} caught my attention: {lead}.{also}",
              "That comes out of a pipeline I built that tracks",
              "data-engineering hiring daily - postings from company career",
              "boards, visa filings from the Department of Labor, and a",
              "per-technology demand index I've been accumulating because",
              "nobody publishes one.",
              f"Your page is at {company_url(company)} if you want to check the",
              "figures."),
        _para(_intro(me),
              "Not asking you to forward a resume - I'd genuinely value your read",
              f"on whether this holds up against how {company} actually works."),
    ])


def email(company: str, insights: list) -> str:
    """Cold email. ~105 words, and the subject line carries the fact."""
    lead, _ = _lead(insights)
    me = _sender()
    subject = f"{possessive(company)} hiring, from the data side"
    body = "\n\n".join([
        "Hi,",
        _para("I maintain a public dataset on data-engineering hiring, and",
              f"{company} stood out: {lead}. The page is {company_url(company)}",
              "- every figure there traces back to a query."),
        _para("I built it end to end: daily ingestion from company career boards",
              "into a warehouse, dbt models, and a per-technology demand index",
              "accumulated daily because no public source has one."),
        _para(_intro(me),
              "Rather than a resume, I'd value your opinion: does this match how",
              f"hiring actually looks from inside {company}?"),
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
               you: dict | None = None, *, owner: bool = False) -> dict:
    """
    The three messages for one company, for whoever is sending them.

    `owner` is explicit and defaults to FALSE, which is the whole point. It used
    to be inferred from whether a sender happened to be supplied, so a visitor who
    filled in nothing looked exactly like the owner and was handed the owner's
    message, his school and a link to his project. Absence of information now
    means the safe thing rather than the revealing thing, and a caller that wants
    the owner's voice has to say so in as many words.
    """
    facts = _fetch_facts(cur, company)
    if not facts or not facts.get("roles"):
        return {"company": company, "error": "no postings stored"}
    insights = build_insights(company, facts, peers, market, days,
                              voice="owner" if owner else "neutral")
    if not insights:
        return {"company": company, "error": "no usable insight"}

    out = {
        "company": company,
        "insights": [{"tier": i.tier, "kind": i.kind, "text": i.text,
                      "evidence": i.evidence} for i in insights],
    }
    if owner:
        # The link belongs in the owner's messages and in nobody else's, so it is
        # not merely omitted from a visitor's text - it is never computed on that
        # path, and therefore cannot reach the model's prompt either.
        out["url"] = company_url(company)
        out["connection"] = connection_note(company, insights)
        out["followup"] = followup(company, insights)
        out["email"] = email(company, insights)
        return out

    from visitor import messages  # noqa: PLC0415
    out.update(messages(company, insights, you))
    return out


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
        d = drafts_for(cur, name, peers, market, days, owner=True)
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
