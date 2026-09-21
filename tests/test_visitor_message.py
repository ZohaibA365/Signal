"""
A visitor's message is the visitor's. The test that did not exist.

The public page writes outreach for whoever is using it, and four separate times it
wrote the owner's instead - his voice, his university, and a link to his project -
in a message a stranger was about to put their name on. Each fix was real and each
one was followed by another report of the same thing, because the leak had more than
one route and nothing checked the finished text.

The worst route needed no bug at all. A visitor who left the optional boxes empty
produced an empty sender; the empty sender was falsy; falsy meant "nobody was
given, so use the configured profile"; and the configured profile is the owner's.
Every gate downstream then agreed this was the owner, including the rail whose only
job is to catch the owner's voice in somebody else's message. That case is the first
test here.

Two kinds of check, because one of them alone has been shown not to hold:

  - behavioural: run the composer over the shapes a real form produces
  - structural: read outreach/visitor.py itself and prove no owner content is in it

The second is the one that survives a refactor. It is not possible to leak a
sentence that is not written anywhere on the path.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("storage", "outreach", "ai_layer", "agent", "service"):
    sys.path.insert(0, str(ROOT / sub))

os.environ.setdefault("SIGNAL_PROFILE", "student")

from candidate_profile import PROFILE  # noqa: E402
from compose import SITE_URL  # noqa: E402
from visitor import messages  # noqa: E402

VISITOR_SOURCE = ROOT / "outreach" / "visitor.py"

# Everything that would mark a message as the owner's. The profile is read rather
# than transcribed, so this cannot drift away from the thing it is protecting.
OWNER_MARKERS = (
    SITE_URL,
    PROFILE["school"],            # "University of Waterloo"
    PROFILE["program"],           # "Management Engineering"
    "I built", "I maintain", "I track", "I build", "I've been accumulating",
    "pipeline I built", "zohaiba365",
)


class _Insight:
    """An insight record, in the shape build_insights returns."""

    def __init__(self, text, tier="peer", kind="stack_emphasis", evidence=None):
        self.text, self.tier, self.kind = text, tier, kind
        self.evidence = evidence or {}


INSIGHTS = [
    _Insight("they mention Kubernetes in 20% of their postings, about 3.6x the "
             "rate across comparable companies",
             evidence={"own_share": 0.20, "peer_share": 0.056}),
    _Insight("370 of their 1,204 open roles were posted in the last 30 days",
             tier="company", kind="recent_volume",
             evidence={"last_30d": 370, "roles": 1204}),
]

# Every shape the form can actually produce, including the ones that used to
# collapse into the owner.
FORM_SHAPES = {
    "nothing filled in": {},
    "no sender at all": None,
    "only whitespace": {"name": "   ", "program": "", "school": "  "},
    "only a name": {"name": "Alex"},
    "a student": {"name": "Priya Raman", "program": "CS student",
                  "school": "McGill", "term": "Summer 2027 internship"},
    "not a student": {"name": "Chiff", "program": "Ai Engineer",
                      "school": "Uwaterloo", "term": "Winter 2027"},
    "a working professional": {"name": "Dana", "program": "data engineer",
                               "school": "Shopify", "term": "a senior platform role"},
    "a career changer": {"name": "Sam",
                         "program": "analyst moving into data engineering",
                         "term": "a first DE job"},
}


@pytest.mark.parametrize("shape", FORM_SHAPES.keys())
@pytest.mark.parametrize("channel", ["email", "connection", "followup"])
def test_no_variant_carries_the_owner(shape, channel):
    text = messages("Capital One", INSIGHTS, FORM_SHAPES[shape])[channel]
    for marker in OWNER_MARKERS:
        assert marker.lower() not in text.lower(), (
            f"the {channel} for '{shape}' contains {marker!r}")


@pytest.mark.parametrize("shape", FORM_SHAPES.keys())
def test_no_variant_carries_any_link(shape):
    """
    Not even the correct one. The dataset is not the sender's to hand over: a
    recipient who clicks it lands on a third person's project, offered as the
    sender's own credential by a message that never says whose it is.
    """
    for text in messages("Capital One", INSIGHTS, FORM_SHAPES[shape]).values():
        assert "http" not in text


def test_the_blank_form_still_produces_a_sendable_email():
    """
    The case that caused all of this. Nothing filled in is ordinary, not an error,
    and it must produce a real message - just one without a sentence about the
    sender.
    """
    email = messages("Capital One", INSIGHTS, {})["email"]
    assert "Capital One" in email
    assert email.startswith("Subject:")
    # Some real observation from the warehouse reached the page. It used to be the
    # Kubernetes ratio; a visitor's lead now ranks that below a plain count, and
    # pinning the specific sentence here would just pin the ranking twice.
    assert any(i.text[:24] in email for i in INSIGHTS)


@pytest.mark.parametrize("field,typed,expected", [
    ("program", "Ai Engineer", "I'm an Ai Engineer"),
    ("program", "CS student", "I'm a CS student"),
    # A field of study is not a person and takes no article. This is the sentence
    # "I'm a Statistics at UBC" used to produce, on the live console, for every
    # visitor who answered "What you do" with their degree instead of a job title.
    ("program", "Statistics", "I'm in Statistics"),
    ("program", "Industrial Design", "I'm in Industrial Design"),
    ("program", "Data Scientist", "I'm a Data Scientist"),
    ("term", "Summer 2027 internship", "looking for a Summer 2027 internship"),
    ("term", "Winter 2027", "looking for Winter 2027"),
    ("term", "a senior platform role", "looking for a senior platform role"),
    ("name", "Chiff", "Chiff"),
])
def test_what_was_typed_is_what_appears(field, typed, expected):
    """
    Their words, not ours. The template used to wrap whatever was typed in
    "student" and "term", so "Ai Engineer" became "a Ai Engineer student" - the
    wrong article and a job title recast as a degree.
    """
    email = messages("Capital One", INSIGHTS, {field: typed})["email"]
    assert expected in email


def test_the_connection_note_fits_linkedin():
    """Including the longest thing a visitor can type into every field."""
    from compose import CONNECTION_LIMIT

    longest = {"name": "x" * 60, "program": "y" * 80, "school": "z" * 80,
               "term": "w" * 40}
    note = messages("Capital One", INSIGHTS, longest)["connection"]
    assert len(note) <= CONNECTION_LIMIT


class TestTheModuleItselfCannotLeak:
    """
    Read the source, not the output.

    Behavioural tests only cover the inputs somebody thought of, and every previous
    fix here passed the tests that existed at the time. These check the property
    directly: there is no owner content in the file, so there is none to emit.
    """

    def test_no_owner_string_is_written_anywhere_in_it(self):
        tree = ast.parse(VISITOR_SOURCE.read_text())
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        # The module docstring explains the bug and necessarily quotes it.
        literals = [s for s in literals if s is not ast.get_docstring(tree)]

        for text in literals:
            for marker in (SITE_URL, PROFILE["school"], PROFILE["program"],
                           "zohaiba365"):
                assert marker.lower() not in text.lower(), (
                    f"visitor.py contains the owner marker {marker!r}")

    def test_it_cannot_reach_the_owner_profile_or_build_a_url(self):
        """
        candidate_profile is where the owner's identity lives and company_url is
        how his link is built. Importing neither is what makes the leak structural
        rather than a thing to remember.
        """
        tree = ast.parse(VISITOR_SOURCE.read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        assert "candidate_profile" not in imported
        assert "company_url" not in imported
        assert "PROFILE" not in imported
        assert "SITE_URL" not in imported


class TestTheRouteThatActuallyLeaked:
    """
    The two lines that turned a blank form into the owner's message.

    Neither is in visitor.py, so the tests above would all have passed while the
    bug was live. This is the route itself: an empty form must stay empty, and
    "nobody told us who is sending" must never resolve to the owner.
    """

    def test_an_empty_form_is_an_empty_sender_not_a_missing_one(self):
        from sender import clean_sender  # noqa: PLC0415

        for raw in ({}, None, {"name": "   "}, "not a dict", []):
            out = clean_sender(raw)
            assert isinstance(out, dict), f"{raw!r} produced {out!r}, not a dict"
            assert not out.get("name")

    @pytest.mark.parametrize("given", [None, {}, {"name": "Alex"}])
    def test_the_drafting_context_is_not_the_owner_unless_it_is_told_so(
            self, given, monkeypatch):
        """
        `sender or _sender()` was the whole bug: a falsy sender silently became the
        configured profile, and the profile is the owner's.

        Built here rather than inspected. The first version of this test asserted
        the signature - that `owner` defaults to False and is keyword-only - and it
        passed with the original fail-open line put back, because the default was
        never what failed. What matters is what the object does with a sender it
        was not given.
        """
        import tools as tools_mod  # noqa: PLC0415

        # The queries the constructor runs. None of them bear on ownership.
        monkeypatch.setattr(tools_mod, "load_market", lambda cur: {})
        monkeypatch.setattr(tools_mod, "peer_stats", lambda cur, names: {})
        monkeypatch.setattr(tools_mod, "collection_days", lambda cur: 90)
        monkeypatch.setattr(tools_mod, "baseline_companies", lambda cur, names: names)
        monkeypatch.setattr(tools_mod, "tech_breadth", lambda cur: {})
        monkeypatch.setattr(tools_mod, "load_tech_meta", lambda cur: {})

        context = tools_mod.DraftingContext(None, ["Capital One"], given)

        assert context.owner is False
        assert context.sender.get("school") != PROFILE["school"], (
            "an unfilled form resolved to the owner's profile")
        assert context.sender.get("program") != PROFILE["program"]

    def test_the_command_line_is_the_only_thing_that_gets_the_owner(self, monkeypatch):
        import tools as tools_mod  # noqa: PLC0415

        monkeypatch.setattr(tools_mod, "load_market", lambda cur: {})
        monkeypatch.setattr(tools_mod, "peer_stats", lambda cur, names: {})
        monkeypatch.setattr(tools_mod, "collection_days", lambda cur: 90)
        monkeypatch.setattr(tools_mod, "baseline_companies", lambda cur, names: names)
        monkeypatch.setattr(tools_mod, "tech_breadth", lambda cur: {})
        monkeypatch.setattr(tools_mod, "load_tech_meta", lambda cur: {})

        context = tools_mod.DraftingContext(None, ["Capital One"], owner=True)
        assert context.owner is True
        assert context.sender["school"] == PROFILE["school"]
    def test_the_service_never_asks_for_the_owner(self):
        """
        No CALL in the public service may claim ownership.

        Parsed rather than grepped: the first version of this searched the text and
        caught a comment saying the service does not do this, which is true and is
        not a call.
        """
        tree = ast.parse((ROOT / "service" / "app.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "owner":
                    assert isinstance(kw.value, ast.Constant) and kw.value.value is False, (
                        f"service/app.py:{node.lineno} claims ownership")


class TestNoStatisticsInAVisitorsEmail:
    """
    The complaint that prompted the rewrite.

    The email opened with "they mention Kubernetes in 20% of their postings, about
    3.6x the rate across comparable companies" - a ratio, identical in shape for
    every company and every sender. The fix is upstream, in build_insights, which
    does not generate either ratio kind for a visitor. This is the guard that says
    so from the other end: whatever the composer does, the finished message has no
    percentage and no multiple in it.
    """

    RATIO_MARKERS = ("%", "x the rate", "x the median", "times the rate")

    # What build_insights actually produces for a visitor: the unusual tool, and
    # plain counts. The module-level INSIGHTS above deliberately still contains the
    # old ratio sentence, because the owner's drafts legitimately use it - and
    # feeding it to the visitor composer proves only that the composer prints what
    # it is given, which it does. The guarantee is upstream, where that kind is
    # never generated for a visitor (tests/test_rare_tool.py), and this is the
    # other end of it: given a visitor's real insight set, nothing statistical
    # survives into any of the three messages.
    VISITOR_INSIGHTS = [
        _Insight("they are one of the few companies in this dataset that mention "
                 "Kubeflow in job postings at all",
                 tier="peer", kind="rare_tool",
                 evidence={"tech": "kubeflow", "companies_mentioning": 30,
                           "corpus_companies": 3922, "their_postings": 12}),
        _Insight("their largest open team is Engineering with 678 roles",
                 tier="company", kind="team_focus",
                 evidence={"department": "Engineering", "roles": 678}),
        _Insight("they are hiring across 14 states",
                 tier="company", kind="geography", evidence={"states": 14}),
    ]

    @pytest.mark.parametrize("shape", FORM_SHAPES.keys())
    @pytest.mark.parametrize("channel", ["email", "connection", "followup"])
    def test_no_percentage_or_multiple_reaches_the_reader(self, shape, channel):
        text = messages("Capital One", self.VISITOR_INSIGHTS,
                        FORM_SHAPES[shape])[channel]
        for marker in self.RATIO_MARKERS:
            assert marker not in text.lower(), (
                f"the {channel} for '{shape}' contains {marker!r}")

    def test_the_unusual_tool_leads_when_there_is_one(self):
        """
        Ordering, not availability: a rare_tool insight must beat a plain count
        even when the count is listed first.
        """
        from visitor import _visitor_lead  # noqa: PLC0415

        volume = _Insight("140 of their 900 open roles were posted in the last 30 days",
                          tier="company", kind="recent_volume")
        rare = _Insight("they are one of the few companies in this dataset that "
                        "mention Kubeflow in job postings at all",
                        tier="peer", kind="rare_tool")
        lead, _ = _visitor_lead([volume, rare])
        assert "Kubeflow" in lead

    def test_a_company_with_no_unusual_tool_still_gets_an_email(self):
        """The silent fallback: an ordinary count, not an empty message."""
        from visitor import _visitor_lead  # noqa: PLC0415

        volume = _Insight("140 of their 900 open roles were posted in the last 30 days",
                          tier="company", kind="recent_volume")
        lead, _ = _visitor_lead([volume])
        assert "900 open roles" in lead


class TestTheLeakRailDoesNotBlockHonestVisitors:
    """
    The backstop that scans an outgoing draft for the owner's text.

    It originally matched the owner's school and degree as well, which refused the
    draft of any Waterloo Management Engineering student who typed their own real
    details - the classmates most likely to open the page. A rail that stops the
    honest case and catches the dishonest one no better is worse than no rail, so
    it now matches only what is genuinely the owner's: the dataset's address, and
    the claim to have built it.
    """

    @staticmethod
    def traces(text: str):
        from leak import owner_traces  # noqa: PLC0415

        return owner_traces(text)

    @pytest.mark.parametrize("sentence", [
        "I'm a Management Engineering student at University of Waterloo, "
        "looking for a Winter 2027 co-op.",
        "I'm a CS student at McGill, looking for a Summer 2027 internship.",
        "I'm an Ai Engineer at Uwaterloo.",
    ])
    def test_a_visitors_own_details_are_never_a_leak(self, sentence):
        assert self.traces(sentence) == []

    @pytest.mark.parametrize("sentence", [
        "I built a dataset tracking hiring trends across major employers.",
        "I maintain a public dataset on data-engineering hiring.",
        "The page is https://zohaiba365.github.io/Signal/companies/oracle/",
    ])
    def test_the_owners_own_text_is_still_caught(self, sentence):
        assert self.traces(sentence), f"leak not caught: {sentence!r}"
