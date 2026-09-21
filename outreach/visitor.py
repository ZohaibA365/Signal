"""
The outreach messages for somebody using the public page. Not the owner's.

This module exists because the other way round did not work. A visitor's message
used to be the owner's message with the owner subtracted from it - fifteen
`if _authored(me):` branches in compose.py and a find-and-replace table that
rewrote "the companies I track" after the fact. It leaked four separate times, and
each fix was a new branch guarding a new sentence. The design guaranteed that:
every sentence written for the owner was a leak waiting to be forgotten, and the
flag deciding which voice to use failed OPEN - absence of the flag meant "this is
the owner", so a visitor who filled in nothing at all got the owner's email, his
school, and a link to his project.

So the subtraction is gone. There is nothing here to subtract. This module knows
about exactly two things - what the warehouse observed about a company, and the
four fields a visitor typed - and it cannot say anything about the owner because
no sentence in it mentions him. It does not import candidate_profile, and it never
builds a URL. tests/test_visitor_message.py enforces both by reading this file.

What a visitor may honestly claim, and the whole reason the wording is what it is:
they read something in a public dataset. They did not build it, they do not
maintain it, and it is not theirs to link to - sending a stranger to somebody
else's project as though it were your own credential misrepresents you to the one
person you are trying to impress.

    from visitor import messages
    messages("Capital One", insights, {"name": "Priya", "program": "CS student"})
"""

from __future__ import annotations

# Deliberately a small, explicit import list. Everything taken from compose is a
# pure text helper with no notion of who is sending - a possessive apostrophe, a
# paragraph joiner, the choice of which insight leads. The moment something here
# needs a profile, it belongs in compose.py instead.
from compose import CONNECTION_LIMIT, _para, possessive

# What a visitor's message opens with, best first. rare_tool is the whole point -
# the thing about this employer that a general-purpose model could not tell them,
# because knowing it requires having looked at every other employer. The rest are
# ordinary counts. No ratio appears here at all: build_insights does not generate
# one for a visitor, and this is the second place that is true.
LEAD_ORDER = ("rare_tool", "team_focus", "leading_tech", "geography",
              "pace_change", "recent_volume")


def _visitor_lead(insights: list) -> tuple[str, str | None]:
    """
    The opening fact, and a second one that says something different.

    compose._lead() ranks by tier and was written for the owner's messages, where
    a peer ratio is the strongest thing available. A visitor gets no ratios, so
    ranking is by what actually reads well to a stranger: the unusual tool first,
    then what they are hiring and where.
    """
    if not insights:
        return "", None

    def rank(insight) -> int:
        kind = getattr(insight, "kind", "")
        return LEAD_ORDER.index(kind) if kind in LEAD_ORDER else len(LEAD_ORDER)

    ordered = sorted(insights, key=rank)
    lead = ordered[0]
    second = next((i.text for i in ordered[1:]
                   if i.kind != lead.kind and i.tier != lead.tier), None)
    return lead.text, second

# One phrase, defined once, for where the numbers came from. A visitor can say
# they were reading a public dataset; they cannot say whose it is, because they do
# not know, and they cannot link it, because it is not theirs to offer.
SOURCE_EMAIL = "I was looking through a public dataset on data-engineering hiring, and"
SOURCE_CONNECTION = "I was reading a public dataset on data-engineering hiring and"
SOURCE_FOLLOWUP = ("That comes out of a public dataset tracking data-engineering "
                   "hiring daily - postings from company career boards, visa "
                   "filings from the Department of Labor, and a per-technology "
                   "demand index.")

# Said when somebody gave no details about themselves at all. Blank fields are the
# common case, not an error: the boxes are optional and the page says so.
NO_DETAILS = "Getting in touch because it seemed worth asking."


def _article(phrase: str) -> str:
    """"an Ai Engineer", "a CS student". Crude, and right far more often than not."""
    return "an" if phrase[:1].lower() in "aeiou" else "a"


# Nouns that need an article in front of them when somebody types a bare one.
# "Looking for Summer 2027" is a sentence; "looking for Summer 2027 internship" is
# not. Deliberately a short list of concrete nouns rather than anything clever: a
# wrong guess here puts a wrong word in a message a person actually sends, so the
# rule fires only where the answer is unambiguous and leaves everything else alone.
# Singular only - a plural is already a complete noun phrase, and "an internships"
# is exactly the mistake this is here to avoid.
COUNTABLE = ("internship", "co-op", "coop", "role", "job", "position",
             "placement", "term", "gig", "traineeship")

DETERMINERS = ("a", "an", "the", "my", "any", "some", "another", "one")


def _needs_article(phrase: str) -> str:
    # Determiners only. "full-time" is an adjective and still wants an article in
    # front of it - "looking for full-time data role" was the bug that made this
    # list explicit rather than a guess at what a leading word might be.
    if phrase.split(" ", 1)[0].lower() in DETERMINERS:
        return phrase
    if phrase.rstrip(".").split(" ")[-1].lower() in COUNTABLE:
        return f"{_article(phrase)} {phrase}"
    return phrase


# Head nouns that name a person. Somebody typing one of these has described who
# they are and the phrase takes an article; anything else is read as a field, which
# does not. Same discipline as COUNTABLE above: an explicit short list, because the
# cost of guessing wrong is a wrong word in a message a person actually sends.
# "a Statistics" was that wrong word - the article was applied unconditionally, so
# every visitor who typed their degree rather than a job title got it.
PERSON_NOUNS = ("student", "engineer", "developer", "scientist", "analyst",
                "researcher", "designer", "manager", "consultant", "architect",
                "intern", "grad", "graduate", "undergraduate", "major",
                "programmer", "practitioner", "specialist")


def _names_a_person(phrase: str) -> bool:
    return phrase.rstrip(".,").split(" ")[-1].lower().rstrip("s") in (
        n.rstrip("s") for n in PERSON_NOUNS)


def _about(you: dict) -> str:
    """
    "I'm a CS student at McGill," or "I'm in Statistics at UBC," or nothing.

    The words are used exactly as given. The page asks "What you do" and "Where",
    and the answers already contain their own nouns; wrapping them in "student"
    regardless turned "Ai Engineer" into "I'm a Ai Engineer student at Uwaterloo" -
    the wrong article, a job title recast as a degree, and a description of
    somebody the sender is not.

    Which of the two frames applies is decided by the head noun and nothing else.
    "Data Engineer" names a person and takes an article; "Statistics" and
    "Industrial Design" name a field and take "in", because "I'm a Statistics at
    UBC" is not a sentence. "in" is also the one preposition that stays true either
    way: it says where they are without asserting they are a student, which a
    professional typing their discipline is not.
    """
    program = (you.get("program") or "").strip()
    school = (you.get("school") or "").strip()
    if program:
        me = (f"I'm {_article(program)} {program}" if _names_a_person(program)
              else f"I'm in {program}")
        return f"{me} at {school}," if school else f"{me},"
    if school:
        return f"I'm at {school},"
    return ""


def _wants(you: dict) -> str:
    """"looking for a Summer 2027 internship", in the visitor's own words."""
    want = (you.get("term") or "").strip()
    return f"looking for {_needs_article(want)}" if want else ""


def _intro(you: dict) -> str:
    """Who the sender is and what they want, as one sentence, or nothing at all."""
    about, wants = _about(you), _wants(you)
    if about and wants:
        return f"{about} {wants}."
    if about:
        return about.rstrip(",") + "."
    if wants:
        return f"I'm {wants}."
    return ""


def _signature(you: dict) -> list[str]:
    name = (you.get("name") or "").strip()
    return [name] if name else []


def email(company: str, insights: list, you: dict) -> str:
    """Cold email. ~105 words, and the subject line carries the fact."""
    lead, _ = _visitor_lead(insights)
    return "Subject: {}\n\n{}".format(
        f"{possessive(company)} hiring, from the data side",
        "\n\n".join([
            "Hi,",
            _para(SOURCE_EMAIL, f"{company} stood out: {lead}."),
            _para(_intro(you) or NO_DETAILS,
                  "Rather than a resume, I'd value your opinion: does this match how",
                  f"hiring actually looks from inside {company}?"),
            *_signature(you),
        ]))


def connection_note(company: str, insights: list, you: dict) -> str:
    """LinkedIn connection note. Hard 300-character cap, so one fact only."""
    lead, _ = _visitor_lead(insights)
    intro = _intro(you).rstrip(".")
    note = (f"Hi - {SOURCE_CONNECTION} {company} came up: {lead}. "
            f"{intro}{' - ' if intro else ''}would value your read on it.")
    if len(note) > CONNECTION_LIMIT:
        # Drop the sender's own description before touching anything factual: the
        # observation is what earns the accept.
        note = (f"Hi - {SOURCE_CONNECTION} {company} came up: {lead} "
                f"- would value your read.")
    if len(note) > CONNECTION_LIMIT:
        note = note[:CONNECTION_LIMIT - 1].rsplit(" ", 1)[0] + "…"
    return note


def followup(company: str, insights: list, you: dict) -> str:
    """Sent after a connection request is accepted. ~120 words."""
    lead, second = _visitor_lead(insights)
    # `second` is a clause about the company ("220 of their 997 open roles were
    # posted in the last 30 days"), so it needs a connector that takes a clause.
    # "They also showed up as <clause>" does not parse.
    also = f" On top of that, {second}." if second else ""
    return "\n\n".join([
        "Thanks for connecting.",
        _para(f"The reason {company} caught my attention: {lead}.{also}",
              SOURCE_FOLLOWUP),
        _para(_intro(you) or NO_DETAILS,
              "Not asking you to forward a resume - I'd genuinely value your read",
              f"on whether this holds up against how {company} actually works."),
    ])


def messages(company: str, insights: list, you: dict | None) -> dict[str, str]:
    """
    All three variants for one company, in the visitor's voice.

    `you` may be None or empty - somebody who filled in nothing still gets a
    sendable message, just without the sentence about themselves. That case used
    to be the worst one: an empty sender fell through to the owner's profile and
    the visitor was handed the owner's email.
    """
    you = you or {}
    return {
        "email": email(company, insights, you),
        "connection": connection_note(company, insights, you),
        "followup": followup(company, insights, you),
    }
