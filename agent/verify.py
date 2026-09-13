"""
Prove a model-written draft against the records it was supposed to be written from.

outreach/compose.py guarantees that "a message cannot contain a figure the
warehouse cannot reproduce", because it assembles messages deterministically from
Insight records that came out of SQL. Letting a model write the prose instead buys
better sentences and throws that guarantee away - unless something checks.

This is that something. It does not ask whether the draft reads well. It asks
whether every checkable claim in it traces back to an insight, and it fails closed:
a draft that cannot be proved is discarded and the deterministic template is used,
so an unverifiable figure never reaches a person.

What it can and cannot do, stated plainly because the distinction matters in an
interview. It proves that every number in the text appears in the source records.
It cannot prove the sentence around the number is a fair reading of it - "hiring
has collapsed to 12 roles" and "hiring is up to 12 roles" both verify. Numeric
grounding is necessary, not sufficient.

Pure functions, no I/O, no API, so every check is testable without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Anything that looks like a quantity: 17, 3.1, 1,029, 17%, 3.1x, $85,000.
NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?x?", re.IGNORECASE)

# URLs are stripped before numbers are counted. The site's own domain contains a
# number, so leaving links in made every draft fail with an untraceable "365" -
# found by the first sanity check written against this module. The link itself is
# checked separately and more strictly, against the exact expected URL.
URL = re.compile(r"https?://\S+")

# Numbers a draft may always contain, because they are not claims about the
# employer. Years bound the co-op term; small integers appear in ordinary prose
# ("one line", "ten seconds") and carry no factual weight.
ALWAYS_ALLOWED = {"2026", "2027", "2028", "1", "2", "3", "4"}

# Phrases that would make the message a lie about the sender's relationship to the
# company. The playbook's whole posture is a stranger offering something checkable;
# claiming prior contact is the one misrepresentation that would matter most.
FORBIDDEN_CLAIMS = (
    "i worked at", "i work at", "i interned at", "when i was at",
    "i applied to", "i applied for", "my application",
    "we spoke", "we met", "as discussed", "following up on our",
    "your recruiter told me", "i was referred",
)

# The playbook forbids these outright: a requisition number turns a message into a
# ticket, per outreach/compose.py's rules.
REQ_NUMBER = re.compile(r"\b(req(uisition)?\.?\s*#?\s*\d+|job\s*id\s*#?\s*\d+)", re.IGNORECASE)


@dataclass
class Verification:
    """The verdict on one draft, and everything needed to explain it."""

    passed: bool
    checks_run: int
    failures: list[str] = field(default_factory=list)
    # Numbers found in the text that trace to nothing. Named individually because
    # "verification failed" is not actionable and "the draft claims 47% and no
    # insight mentions 47" is.
    untraceable_numbers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks_run": self.checks_run,
            "failures": self.failures,
            "untraceable_numbers": self.untraceable_numbers,
        }


# Characters prose runs onto the end of a link, so a URL is cut at the first of
# them. Deliberately NOT a colon or a slash: the first version included the colon
# and cut every link at "https", failing everything including the correct one.
# A question mark is here because these URLs carry no query string, and a link at
# the end of a question is the common case.
URL_GLUE = "\u2014\u2013\u2026,;!?\"')]"


def _clean_url(token: str) -> str:
    for ch in URL_GLUE:
        token = token.split(ch)[0]
    return token.rstrip(".-")


def _normalise(token: str) -> str:
    """'$85,000' and '85000' are the same number; '17%' and '17' are too."""
    return token.lower().lstrip("$").rstrip("%x").replace(",", "").rstrip(".")


def allowed_numbers(insights: list[dict], sender: dict | None = None) -> set[str]:
    """
    Every number a draft is permitted to contain.

    Drawn from two places, and the second is the important one. The evidence dicts
    hold the raw values, but the insight TEXT holds values derived from them that
    appear nowhere else - a pace comparison stores company_last_30d and peer_median
    and writes "3.1x", a stack insight stores own_share 0.17 and writes "17%". The
    deterministic template's own output is therefore the ground truth for what may
    appear, so both are harvested.
    """
    allowed = set(ALWAYS_ALLOWED)
    for insight in insights:
        for value in (insight.get("evidence") or {}).values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            allowed.add(_normalise(str(value)))
            # A share stored as 0.17 is written as 17%, and a rounded integer is
            # written where a float is stored.
            allowed.add(_normalise(str(round(value))))
            allowed.add(_normalise(str(round(value * 100))))
            allowed.add(_normalise(f"{value:.1f}"))
        for token in NUMBER.findall(insight.get("text") or ""):
            allowed.add(_normalise(token))
            # A correct rounding of a true figure is still true. An insight reading
            # "about 12.1x the rate" permits "12x": the reader is not misled, and
            # rejecting it sent an otherwise perfect draft to the template, which
            # is a real cost for no safety gain. Note this admits only ROUNDINGS of
            # a real value - 18% is still refused when the figure is 17%, because
            # 17 does not round to 18.
            try:
                value = float(_normalise(token))
            except ValueError:
                continue
            allowed.add(_normalise(str(round(value))))
            allowed.add(_normalise(f"{value:.1f}"))

    for value in (sender or {}).values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(_normalise(str(value)))
        elif isinstance(value, str):
            for token in NUMBER.findall(value):
                allowed.add(_normalise(token))
    return {a for a in allowed if a}


def check_numbers(text: str, insights: list[dict], sender: dict | None = None) -> list[str]:
    """Every number in the draft must trace to an insight. Returns the ones that do not."""
    allowed = allowed_numbers(insights, sender)
    untraceable = []
    for token in NUMBER.findall(URL.sub(" ", text or "")):
        value = _normalise(token)
        if value and value not in allowed:
            untraceable.append(token)
    return untraceable


def verify_draft(text: str, *, company: str, url: str, insights: list[dict],
                 sender: dict | None = None, max_chars: int | None = None,
                 max_words: int | None = None) -> Verification:
    """
    Run every check against one draft. Fails closed: any failure means reject.

    max_chars and max_words come from the channel the draft is for, since a
    LinkedIn note over 300 characters cannot be sent at all.
    """
    failures: list[str] = []
    checks = 0
    body = text or ""

    # 1. Numbers must be traceable. The check this exists for.
    checks += 1
    untraceable = check_numbers(body, insights, sender)
    if untraceable:
        failures.append(f"numbers that trace to no insight: {', '.join(untraceable)}")

    # 2. The URL must be exact. A plausible invented link is the most damaging
    #    hallucination available here, because it is the one thing in the message
    #    the reader is explicitly invited to click.
    checks += 1
    # Punctuation glued to a link has to be CUT, not stripped. A model wrote the
    # correct URL followed immediately by an em-dash - "/rbc/\u2014does this hold
    # up?" - and \\S+ swallowed the next word too, so the draft was rejected for a
    # link that was right. Stripping trailing characters cannot help when the
    # offending character is in the middle of what the pattern matched.
    urls = [_clean_url(u) for u in re.findall(r"https?://\S+", body)]
    stray = [u for u in urls if u != url]
    if stray:
        failures.append(f"link is not the company's page: {', '.join(stray)}")

    # 3. The company must be named. Deliberately not also checking that no OTHER
    #    employer is named: doing that properly needs the full list of company
    #    names to test against, and a half-version - matching a handful of known
    #    names - would read like a guarantee while missing most of the cases.
    checks += 1
    if company and company.lower() not in body.lower():
        failures.append(f"the draft never names {company}")

    # 4. Channel budgets, so a draft is not rejected later by the channel itself.
    checks += 1
    if max_chars is not None and len(body) > max_chars:
        failures.append(f"{len(body)} characters, over the {max_chars} limit")
    if max_words is not None and len(body.split()) > max_words:
        failures.append(f"{len(body.split())} words, over the {max_words} limit")

    # 5. Playbook prohibitions and misrepresentation.
    checks += 1
    lowered = body.lower()
    for phrase in FORBIDDEN_CLAIMS:
        if phrase in lowered:
            failures.append(f"claims a relationship that does not exist: {phrase!r}")
    if REQ_NUMBER.search(body):
        failures.append("contains a requisition number, which the playbook forbids")

    return Verification(passed=not failures, checks_run=checks, failures=failures,
                        untraceable_numbers=untraceable)
