"""
The opening line of a visitor's email, and the claim it is allowed to make.

The email used to open with a ratio - "they mention Kubernetes in 20% of their
postings, about 3.6x the rate across comparable companies". Same shape for every
company and every sender, and readable as a statistic rather than as something a
person noticed. It now opens with what is unusual about the employer: a tool almost
nobody else in the index mentions.

That sentence is a factual claim about 3,900 other companies, so the interesting
tests are the ones about when it must NOT be made. "One of the few companies that
mention Dask" is worth reading precisely because it is rare, and worthless - and
wrong - the moment it is said about something common.

Two floors, and both matter:
  - at most 2% of companies mention it, or "one of the few" is false
  - the company itself has at least three postings that do, or a single stray
    posting becomes the headline fact about an employer with two thousand roles

Pure functions over dicts, so no database.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("storage", "ai_layer", "outreach"):
    sys.path.insert(0, str(ROOT / sub))

from fields import categories_for  # noqa: E402
from insights import (  # noqa: E402
    RARE_TOOL_MAX_SHARE,
    RARE_TOOL_MIN_POSTINGS,
    build_insights,
    rare_tools,
)

CORPUS = 4000

TECH_META = {
    "dask":       {"name": "Dask", "category": "transform", "is_ubiquitous": False},
    "kubeflow":   {"name": "Kubeflow", "category": "ml", "is_ubiquitous": False},
    "quicksight": {"name": "QuickSight", "category": "bi", "is_ubiquitous": False},
    "kubernetes": {"name": "Kubernetes", "category": "infra", "is_ubiquitous": False},
    "python":     {"name": "Python", "category": "language", "is_ubiquitous": True},
}


def breadth(**per_tech: int) -> dict:
    return {"companies": CORPUS, "per_tech": per_tech}


class TestWhenTheClaimMayBeMade:
    def test_a_genuinely_rare_tool_is_offered(self):
        tools = rare_tools([("dask", 42)], breadth(dask=10), frozenset(), TECH_META)
        assert [t["slug"] for t in tools] == ["dask"]
        assert tools[0]["companies_mentioning"] == 10
        assert tools[0]["corpus_companies"] == CORPUS

    def test_a_common_tool_is_not(self):
        """Kubernetes at a quarter of the index is not "one of the few"."""
        assert rare_tools([("kubernetes", 300)], breadth(kubernetes=1000),
                          frozenset(), TECH_META) == []

    def test_the_share_floor_is_where_it_says_it_is(self):
        just_over = int(CORPUS * RARE_TOOL_MAX_SHARE) + 1
        just_under = int(CORPUS * RARE_TOOL_MAX_SHARE)
        assert rare_tools([("dask", 9)], breadth(dask=just_over),
                          frozenset(), TECH_META) == []
        assert rare_tools([("dask", 9)], breadth(dask=just_under),
                          frozenset(), TECH_META) != []

    def test_one_stray_posting_is_not_a_headline(self):
        """
        A company with two thousand roles, one of which happens to name Dask, is
        not a Dask shop. True and worthless is still worthless.
        """
        assert rare_tools([("dask", RARE_TOOL_MIN_POSTINGS - 1)], breadth(dask=10),
                          frozenset(), TECH_META) == []
        assert rare_tools([("dask", RARE_TOOL_MIN_POSTINGS)], breadth(dask=10),
                          frozenset(), TECH_META) != []

    def test_a_ubiquitous_tool_is_never_offered(self):
        assert rare_tools([("python", 500)], breadth(python=12),
                          frozenset(), TECH_META) == []

    def test_an_empty_corpus_makes_no_claims(self):
        """Nothing to be rare against."""
        assert rare_tools([("dask", 42)], {"companies": 0, "per_tech": {}},
                          frozenset(), TECH_META) == []


class TestTheVisitorsFieldChoosesWhichTool:
    def test_an_in_field_tool_leads_over_a_rarer_one(self):
        """
        Sorting by rarity alone hands an AI engineer a mainframe scheduler because
        six companies mention it. Relevance first, then rarity.
        """
        stack = [("dask", 40), ("kubeflow", 5)]
        tools = rare_tools(stack, breadth(dask=4, kubeflow=30),
                           categories_for("Ai Engineer"), TECH_META)
        assert tools[0]["slug"] == "kubeflow"
        assert tools[0]["in_field"] is True

    def test_without_a_field_the_rarest_wins(self):
        stack = [("dask", 40), ("kubeflow", 5)]
        tools = rare_tools(stack, breadth(dask=4, kubeflow=30),
                           categories_for("mechanical engineering student"),
                           TECH_META)
        assert tools[0]["slug"] == "dask"

    @pytest.mark.parametrize("typed,expected", [
        ("Ai Engineer", "kubeflow"),
        ("data analyst", "quicksight"),
        ("mechanical engineering student", "dask"),
        ("", "dask"),
    ])
    def test_the_same_company_leads_differently_for_different_people(
            self, typed, expected):
        stack = [("dask", 40), ("kubeflow", 20), ("quicksight", 20)]
        tools = rare_tools(stack, breadth(dask=4, kubeflow=30, quicksight=30),
                           categories_for(typed), TECH_META)
        assert tools[0]["slug"] == expected


class TestWhatAVisitorNeverSees:
    """
    The two ratio sentences are not generated for a visitor at all, rather than
    generated and then avoided. Neither the template nor the model can reach for
    something that was never put in the list.
    """

    FACTS = {
        "roles": 200, "last_30d": 60, "prior_30d": 20, "states": 9,
        "departments": 4, "countries": "US",
        "top_departments": [("Engineering", 40)],
        "stack": [("dask", 42), ("kubernetes", 80)],
    }
    PEERS = {"median_last_30d": 5, "total_postings": 100000.0,
             "stack_counts": {"dask": 50.0, "kubernetes": 900.0}}

    def _kinds(self, audience):
        return {i.kind for i in build_insights(
            "Capital One", dict(self.FACTS), dict(self.PEERS), {}, days_collected=90,
            audience=audience, breadth=breadth(dask=10, kubernetes=1000),
            categories=frozenset(), tech_meta=TECH_META)}

    def test_no_ratio_kinds_for_a_visitor(self):
        kinds = self._kinds("visitor")
        assert "stack_emphasis" not in kinds
        assert "pace_vs_peers" not in kinds

    def test_the_rare_tool_is_there_instead(self):
        assert "rare_tool" in self._kinds("visitor")

    def test_the_owner_keeps_the_ratios(self):
        """Nothing about the command-line drafts changes."""
        kinds = self._kinds("owner")
        assert "stack_emphasis" in kinds or "pace_vs_peers" in kinds

    def test_the_sentence_carries_no_number(self):
        """
        The figures belong in the evidence, where they can be audited. The claim a
        recipient reads is a plain one, which is the whole point of the rewrite.
        """
        insight = next(i for i in build_insights(
            "Capital One", dict(self.FACTS), dict(self.PEERS), {}, days_collected=90,
            audience="visitor", breadth=breadth(dask=10),
            categories=frozenset(), tech_meta=TECH_META) if i.kind == "rare_tool")
        assert not any(ch.isdigit() for ch in insight.text)
        assert insight.evidence["companies_mentioning"] == 10
        assert insight.evidence["corpus_companies"] == CORPUS
