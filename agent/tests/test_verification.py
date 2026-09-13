"""
The hallucination check, tested on the ways a draft can lie.

agent/verify.py is what lets a model write the prose without giving up the
guarantee that outreach/compose.py provides by construction: that no figure in a
message is one the warehouse cannot reproduce. If these tests are wrong, the
guarantee is gone and nothing would announce it.

Pure functions throughout, so this needs no key, no network and no database.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from verify import allowed_numbers, check_numbers, verify_draft  # noqa: E402

URL = "https://zohaiba365.github.io/Signal/companies/td-bank/"

INSIGHTS = [
    {"tier": "company", "kind": "recent_volume",
     "text": "12 of their 40 open roles were posted in the last 30 days",
     "evidence": {"last_30d": 12, "roles": 40, "share_pct": 30}},
    {"tier": "peer", "kind": "stack_emphasis",
     "text": ("they mention Apache Spark in 17% of their postings, about 3.1x the "
              "rate across the companies I track"),
     "evidence": {"tech": "spark", "own_share": 0.17, "peer_share": 0.055}},
]
SENDER = {"school": "Waterloo", "program": "Management Engineering",
          "term": "Winter 2027", "role": "Data Engineering"}

GROUNDED = (
    "Subject: TD Bank's hiring, from the data side\n\n"
    "Hi,\n\n"
    "TD Bank stood out: 12 of their 40 open roles were posted in the last 30 days, "
    "and they mention Apache Spark in 17% of postings, about 3.1x the rate across "
    f"the companies I track. The page is {URL} - every figure traces to a query.\n\n"
    "I'm a Management Engineering student at Waterloo looking for a Winter 2027 "
    "term. Does this match how hiring looks from inside TD Bank?"
)


def verify(text, **kw):
    return verify_draft(text, company="TD Bank", url=URL, insights=INSIGHTS,
                        sender=SENDER, **kw)


class TestWhatMustPass:
    def test_a_grounded_draft_passes(self):
        v = verify(GROUNDED)
        assert v.passed, v.failures

    def test_a_correct_rounding_of_a_real_figure_is_allowed(self):
        """
        An insight reading "about 12.1x" permits "12x": the reader is not misled,
        and refusing it sent an otherwise perfect draft to the template. Only
        roundings, though - 13x from 12.1x is still an invention.
        """
        rounded = [{"text": "about 12.1x the rate across the companies I track",
                    "evidence": {"own_share": 0.24, "peer_share": 0.02}}]
        assert check_numbers("TD Bank emphasizes it 12x more", rounded) == []
        assert check_numbers("TD Bank emphasizes it 13x more", rounded) == ["13x"]

    def test_derived_figures_are_allowed(self):
        """
        3.1x and 17% appear in no evidence dict - they are computed by the insight
        and only exist in its text. Harvesting the text as well as the evidence is
        what makes them legal, and getting this wrong would reject every honest
        draft that used the strongest line available.
        """
        allowed = allowed_numbers(INSIGHTS, SENDER)
        assert "3.1" in allowed and "17" in allowed

    def test_numbers_inside_the_link_are_not_claims(self):
        """
        The site's own domain contains 365. Counting it as a claim failed every
        draft, which the first sanity check caught before any of this shipped.
        """
        assert check_numbers(f"See {URL}", INSIGHTS, SENDER) == []

    @pytest.mark.parametrize("tail", [
        "\u2014does this hold up?",   # an em-dash run straight onto the link
        ", and tell me.",
        ".",
        ") for detail.",
        "",
    ])
    def test_punctuation_glued_to_the_link_is_not_a_wrong_link(self, tail):
        """
        A model wrote the correct URL with an em-dash immediately after it, and the
        first version of this check rejected the draft for a link that was right.
        The fix then cut every URL at the colon in "https", failing all of them -
        which is why these cases are pinned rather than reasoned about.
        """
        v = verify(f"TD Bank stood out. See {URL}{tail}")
        assert v.passed, v.failures

    @pytest.mark.parametrize("wrong", [
        "https://zohaiba365.github.io/Signal/td-bank/",     # plausible, wrong path
        "https://tdbank.com/careers",
    ])
    def test_a_link_that_is_actually_wrong_still_fails(self, wrong):
        v = verify(f"TD Bank stood out. See {wrong}")
        assert not v.passed
        assert any("not the company's page" in f for f in v.failures)

    def test_small_numbers_in_ordinary_prose_are_allowed(self):
        assert verify(GROUNDED.replace("Does this", "In one line, does this")).passed


class TestWhatMustFail:
    def test_an_invented_statistic(self):
        v = verify(GROUNDED.replace("12 of their 40", "47 of their 900"))
        assert not v.passed
        assert "47" in v.untraceable_numbers and "900" in v.untraceable_numbers

    def test_a_real_number_rounded_into_a_different_one(self):
        """18% is not 17%, however close. Nudging a figure is still inventing one."""
        v = verify(GROUNDED.replace("17% of postings", "18% of postings"))
        assert not v.passed and "18%" in v.untraceable_numbers

    def test_a_plausible_but_wrong_link(self):
        v = verify(GROUNDED.replace(URL, "https://zohaiba365.github.io/Signal/td-bank/"))
        assert not v.passed
        assert any("not the company's page" in f for f in v.failures)

    def test_a_claimed_relationship(self):
        v = verify(GROUNDED.replace("TD Bank stood out", "I interned at TD Bank and it stood out"))
        assert not v.passed
        assert any("relationship that does not exist" in f for f in v.failures)

    def test_a_requisition_number(self):
        v = verify(GROUNDED + "\n\nRe: req #40821")
        assert not v.passed
        assert any("requisition number" in f for f in v.failures)

    def test_the_company_going_unnamed(self):
        v = verify(GROUNDED.replace("TD Bank", "your team"))
        assert not v.passed
        assert any("never names" in f for f in v.failures)

    def test_a_connection_note_over_the_linkedin_limit(self):
        v = verify("TD Bank. " + "x" * 400, max_chars=300)
        assert not v.passed
        assert any("over the 300 limit" in f for f in v.failures)

    def test_an_email_over_the_word_budget(self):
        v = verify("TD Bank " + "word " * 200, max_words=140)
        assert not v.passed
        assert any("over the 140 limit" in f for f in v.failures)


class TestTheVerdictIsExplainable:
    def test_failures_name_the_offending_claim(self):
        """'Verification failed' is not actionable; the number that failed is."""
        v = verify(GROUNDED.replace("12 of their 40", "63 of their 40"))
        assert "63" in v.untraceable_numbers
        assert v.as_dict()["untraceable_numbers"] == ["63"]

    def test_every_check_runs_even_when_one_fails(self):
        """A draft that fails two ways should report both, not stop at the first."""
        v = verify("I applied to TD Bank and 47% of their roles are open. "
                   "See https://example.com/x")
        assert len(v.failures) >= 3, v.failures
        assert v.checks_run == 5
