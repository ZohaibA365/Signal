"""
Grounded drafting: the model writes the prose, and it is used only if it verifies.

outreach/compose.py assembles messages deterministically, so every figure in them
traces to a query by construction. That reads a little mechanically, and a model
writes better sentences - but only if the guarantee survives, which is what
agent/verify.py is for.

The arrangement:

    insights (from SQL)  ->  model writes three variants
                         ->  verifier proves every number, the link, the naming,
                             the channel budgets and the playbook prohibitions
                         ->  pass: use the model's words
                         ->  fail: log the exact offending claim, use compose.py

So the worst case is the old behaviour, and the log records how often the model
fabricated, which is a number worth having rather than a thing to hope about.

The model is given the insight records and told to use those and nothing else. That
instruction is not the safety mechanism - the verifier is. The instruction only
makes the verifier fire less often.
"""

from __future__ import annotations

import os

# compose._sender() reads the candidate profile, and candidate_profile.py defaults
# to 'cobol', which has no school or programme keys and renders them as empty
# strings - "I'm a  student at  looking for a". The daily pipeline sets this in its
# environment; anything importing compose outside that pipeline has to set it too.
os.environ.setdefault("SIGNAL_PROFILE", "student")

from pydantic import BaseModel, Field  # noqa: E402
from verify import Verification, verify_draft  # noqa: E402

# Channel budgets. compose.CONNECTION_LIMIT is the real LinkedIn cap and is
# imported rather than repeated; the word ceilings are the playbook's own numbers.
EMAIL_MAX_WORDS = 140          # the playbook targets ~105; this is the hard edge
FOLLOWUP_MAX_WORDS = 150


class Drafts(BaseModel):
    """The three channel variants, as structured output."""

    email: str = Field(description="A cold email. Begin with 'Subject: ' on its own line.")
    connection: str = Field(description="A LinkedIn connection note. Hard limit 300 characters.")
    followup: str = Field(description="A short follow-up message, roughly 120 words.")


SYSTEM_PROMPT = """You write short outreach messages for somebody reaching out to a
company, citing a public dataset on data-engineering hiring.

The sender is described below in their own words. Use those words. They may be a
student, they may not be; do not call them a student, assume a degree, a term, or
an internship unless what they wrote says so.

THE RULES, in order. They are not style preferences; a message that breaks one is
discarded.

1. Open with something true and specific about THEM, not about the sender.
2. Say what the sender built, in one line, with the link so they can check it.
3. Ask for their opinion, not their time. "Does this hold up?" is answerable in one
   line; "would you be open to a chat" asks a stranger for a calendar slot.
4. Mention the role last, as interest rather than an application. Never a
   requisition number - it turns a message into a ticket.

THE ABSOLUTE CONSTRAINT ON FACTS

You will be given a list of observations, each with its evidence. Every number you
write must come from that list. You may rephrase an observation, combine two, or
leave one out. You may not introduce a figure that is not there, round one into a
different number, or add a fact you happen to know about the company. If you have
nothing specific to say, write a shorter message.

This is checked mechanically against the observations after you answer. A draft
containing any unsupported number is thrown away, so inventing one costs the
message rather than improving it.

Use only the exact link given, and put a space after it rather than running
punctuation straight onto the end.

Name the company in every one of the three variants. "Your team" reads as though it
could be addressed to anyone, and each variant is checked on its own.

Never claim to have worked at, applied to, or spoken with the company - the sender
is a stranger to them, and the message works because it is useful, not because it
pretends to be familiar."""


def _prompt(company: str, url: str, insights: list[dict], sender: dict) -> str:
    from compose import CONNECTION_LIMIT

    lines = [f"COMPANY: {company}", f"LINK TO THE COMPANY'S PAGE: {url}", "", "OBSERVATIONS:"]
    for i, insight in enumerate(insights, 1):
        lines.append(f"{i}. {insight['text']}")
        if insight.get("evidence"):
            lines.append(f"   evidence: {insight['evidence']}")
    lines += [
        "",
        # Whether the sender built this dataset or is simply citing it. Saying "a
        # pipeline I built" in the name of somebody who did not build it is a lie
        # in a message they may actually send.
        ("THE SENDER built and maintains this dataset."
         if not sender.get("_borrowed") else
         "THE SENDER did NOT build this dataset - they are citing it. Never write "
         "that they built, maintain or run it."),
        "",
        "THE SENDER:",
        f"  name: {sender.get('name') or '(unnamed - do not invent one)'}",
        # Labels rather than a sentence, so the model is handed facts to phrase
        # instead of a phrasing to copy. The old form read "{program} student at
        # {school}", which put the word "student" in front of whatever the sender
        # typed - and they had typed "Ai Engineer". A blank line is left out
        # entirely rather than sent as an empty label, because an empty label
        # invites the model to fill it.
        *([f"  what they do: {sender['program'].strip()}"]
          if sender.get("program", "").strip() else []),
        *([f"  where: {sender['school'].strip()}"]
          if sender.get("school", "").strip() else []),
        *([f"  what they are looking for: {sender['term'].strip()}"]
          if sender.get("term", "").strip() else []),
        *([f"  role of interest: {sender['role'].strip()}"]
          if sender.get("role", "").strip() else []),
        *(["  built: daily ingestion from company career boards into a warehouse,",
           "         dbt models, and a per-technology demand index accumulated daily"]
          if not sender.get("_borrowed") else []),
        "",
        "BUDGETS, which are checked and are not advisory:",
        f"  email: at most {EMAIL_MAX_WORDS} words, and aim for about 105",
        f"  connection: at most {CONNECTION_LIMIT} characters, the LinkedIn limit",
        f"  followup: at most {FOLLOWUP_MAX_WORDS} words, and aim for about 120",
        "",
        "Write the three variants.",
    ]
    return "\n".join(lines)


def verify_all(drafts: dict, company: str, url: str, insights: list[dict],
               sender: dict) -> dict[str, Verification]:
    """Verify each variant against its own channel budget."""
    from compose import CONNECTION_LIMIT

    # A borrowed sender is somebody using the public page rather than the dataset's
    # owner, and their draft may not claim to have built it.
    borrowed = bool(sender.get("_borrowed"))
    common = {"company": company, "url": url, "insights": insights,
              "sender": sender, "borrowed": borrowed}
    return {
        "email": verify_draft(drafts.get("email", ""), max_words=EMAIL_MAX_WORDS,
                              **common),
        "connection": verify_draft(drafts.get("connection", ""),
                                   max_chars=CONNECTION_LIMIT, **common),
        "followup": verify_draft(drafts.get("followup", ""),
                                 max_words=FOLLOWUP_MAX_WORDS, **common),
    }


def generate(client, company: str, url: str, insights: list[dict], sender: dict,
             model: str) -> tuple[dict, object]:
    """Ask the model for three variants. Returns (drafts, usage)."""
    response = client.messages.parse(
        model=model,
        max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 # Stable across every company, so it caches after the first call -
                 # the same trick ai_layer/enrich.py uses.
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _prompt(company, url, insights, sender)}],
        output_format=Drafts,
    )
    parsed = response.parsed_output
    return ({"email": parsed.email, "connection": parsed.connection,
             "followup": parsed.followup}, response.usage)


def drafts_with_fallback(client, template_drafts: dict, sender: dict, model: str
                         ) -> tuple[dict, str, dict, object | None]:
    """
    Model drafts if they verify, the template's if they do not.

    `template_drafts` is what compose.drafts_for() returned - it carries the
    insights, the url, and a deterministic draft of each variant, so the fallback
    costs nothing and is always available.

    Returns (drafts, source, verification_report, usage). Verification runs on the
    model's output only: the template's output is correct by construction, which is
    the entire reason it is the fallback.
    """
    company = template_drafts["company"]
    url = template_drafts["url"]
    insights = template_drafts["insights"]
    template = {k: template_drafts[k] for k in ("email", "connection", "followup")}

    if client is None:
        return template, "template", {"skipped": "no model client"}, None

    try:
        drafts, usage = generate(client, company, url, insights, sender, model)
    except Exception as exc:                                        # noqa: BLE001
        # A model failure is not a run failure. The deterministic draft is right
        # there, so the batch continues with a logged reason.
        return template, "template", {"error": f"{type(exc).__name__}: {exc}"[:200]}, None

    checks = verify_all(drafts, company, url, insights, sender)
    report = {name: v.as_dict() for name, v in checks.items()}
    if all(v.passed for v in checks.values()):
        return drafts, "model", report, usage

    # The rejected text is kept, not discarded. "The model fabricated something"
    # is not a finding you can act on; the sentence it fabricated is, and without
    # it there is no way to tell a hallucination from a verifier that is too
    # strict. It is marked clearly so nothing downstream mistakes it for a draft.
    report["rejected_draft"] = drafts

    # All-or-nothing per posting, deliberately. Mixing a verified model email with
    # a template follow-up would make "who wrote this" unanswerable per message,
    # and that question is the first one worth asking about anything being sent.
    return template, "template", report, usage
