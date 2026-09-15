"""
Turning "what you do" into the kinds of tools a person would care about.

The page takes free text, so this map decides whether an AI engineer is told about
Kubeflow or about a mainframe scheduler. A missed keyword costs a less tailored
opening line and nothing else - no match means no preference, and the rarest tool
at the company leads instead. These tests pin that the failure stays that shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outreach"))

from fields import categories_for  # noqa: E402


@pytest.mark.parametrize("typed,expected", [
    ("Ai Engineer", {"ml"}),
    ("AI Engineer", {"ml"}),
    ("machine learning intern", {"ml"}),
    ("Data Scientist", {"ml"}),
    ("data analyst", {"bi", "warehouse"}),
    ("business intelligence developer", {"bi", "warehouse"}),
    ("devops", {"infra", "cloud"}),
    ("site reliability engineer", {"infra", "cloud"}),
])
def test_a_field_maps_to_its_tools(typed, expected):
    assert categories_for(typed) == expected


def test_data_engineering_covers_the_pipeline_tools():
    assert categories_for("Data Engineer") == {
        "orchestration", "transform", "ingestion", "warehouse", "streaming"}


def test_software_covers_languages_and_infrastructure():
    assert categories_for("CS student") == {"language", "infra", "database"}


@pytest.mark.parametrize("typed", [
    "mechanical engineering student",
    "nurse",
    "history major",
    "",
    "   ",
    None,
])
def test_anything_unrecognised_means_no_preference(typed):
    """
    The common case, and a fine one. It must be an empty set rather than a guess:
    guessing puts an irrelevant tool in the first line of somebody's email.
    """
    assert categories_for(typed) == frozenset()


def test_longer_phrases_win_over_shorter_ones():
    """
    "analyst moving into data engineering" is a data engineer, not an analyst. The
    ordering in FIELD_CATEGORIES is what decides this, so it is pinned here.
    """
    assert categories_for("analyst moving into data engineering") == {
        "orchestration", "transform", "ingestion", "warehouse", "streaming"}


def test_matching_ignores_case_and_surrounding_words():
    assert categories_for("Senior Staff DATA ENGINEER, Platform") == categories_for(
        "data engineer")
