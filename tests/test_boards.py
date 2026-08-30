"""
Tests for the career-board layer.

These cover the decisions that were expensive to get right, not the HTTP.
The failure mode this layer has is specific and bad: a wrong board attaches
another company's jobs to a real company's page, on a site meant to be sent to
that company. So the tests concentrate on identity - which board belongs to
whom, and what gets rejected.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestion"))
sys.path.insert(0, str(ROOT / "storage"))

from ats import (  # noqa: E402
    ADAPTERS,
    GUESSABLE,
    workday,  # noqa: E402
)
from ats.base import clean, slug_variants  # noqa: E402
from board_discovery import canon, is_denied  # noqa: E402

# --------------------------------------------------------------- Workday URLs

def test_workday_url_yields_all_three_coordinates():
    """
    Tenant, host and site all have to come off a real careers URL.

    None of them are guessable: probing eight plausible tenants across six
    hosts and four common site names resolved none of the enterprises in the
    corpus. Capital One is wd12 with site Capital_One - not wd1, not "Careers".
    """
    c = workday.parse_url(
        "https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/job/McLean-VA/x_R1")
    assert c == {"tenant": "capitalone", "host": "wd12", "site": "Capital_One"}


def test_workday_url_without_locale_segment():
    c = workday.parse_url("https://bah.wd1.myworkdayjobs.com/BAH_Jobs")
    assert c == {"tenant": "bah", "host": "wd1", "site": "BAH_Jobs"}


def test_workday_url_rejects_a_non_workday_host():
    assert workday.parse_url("https://boards.greenhouse.io/figma") is None
    assert workday.parse_url("") is None


# ------------------------------------------------------- relative posted dates

def test_workday_relative_dates_resolve():
    """Workday reports recency as prose rather than a date."""
    from datetime import UTC, datetime, timedelta
    today = datetime.now(UTC).date()
    assert workday.parse_posted("Posted Today") == today.isoformat()
    assert workday.parse_posted("Posted Yesterday") == (today - timedelta(days=1)).isoformat()
    assert workday.parse_posted("Posted 7 Days Ago") == (today - timedelta(days=7)).isoformat()


def test_workday_thirty_plus_is_treated_as_a_floor():
    """
    "Posted 30+ Days Ago" is a bound, not a value.

    Recording it as exactly 30 keeps the column typed as a date, and is the
    reason posting-age figures from Workday are treated as approximate rather
    than published as precise.
    """
    from datetime import UTC, datetime, timedelta
    expected = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    assert workday.parse_posted("Posted 30+ Days Ago") == expected


def test_workday_unparseable_date_is_none_not_today():
    """An unknown format must not silently become today and read as fresh."""
    assert workday.parse_posted("Posted recently") is None
    assert workday.parse_posted(None) is None


# ------------------------------------------------------------------ slug names

def test_slug_variants_strip_legal_suffixes():
    v = slug_variants("Capital One, Inc.")
    assert "capitalone" in v and "capital-one" in v


def test_slug_variants_are_unique_and_ordered():
    v = slug_variants("Stripe")
    assert v == list(dict.fromkeys(v))
    assert v[0] == "stripe"


# ------------------------------------------------------------------- denylist

def test_aggregators_are_denied_by_name():
    """
    Jobgether has a real Lever board and is not an employer.

    It republishes other companies' roles under its own name, so 1,368
    postings arrived with the wrong employer and a link to a middleman - the
    exact problem the board switch existed to solve. Nothing in the name
    signals it, so it has to be listed.
    """
    assert is_denied("Jobgether")
    assert is_denied("iSpace, Inc")
    assert is_denied("Next Step Systems")


def test_staffing_firms_are_denied_by_pattern():
    assert is_denied("Acme Staffing")
    assert is_denied("Robert Half")
    assert is_denied("Insight Global")


def test_real_employers_are_not_denied():
    for name in ("Databricks", "Capital One", "Stripe", "Cohere", "SpaceX", "Bosch"):
        assert not is_denied(name), name


def test_canon_ignores_legal_form_and_punctuation():
    assert canon("ServiceNow, Inc.") == canon("ServiceNow")
    assert canon("Capital One") == "capitalone"


# ------------------------------------------------------------------- adapters

def test_every_adapter_exposes_the_same_interface():
    """The sweep and the ingester treat all systems identically."""
    for name, mod in ADAPTERS.items():
        assert hasattr(mod, "fetch"), name
        assert hasattr(mod, "probe"), name
        assert mod.NAME == name


def test_guessable_boards_take_a_single_slug():
    """
    Only slug-shaped boards can be swept blind.

    Workday is excluded on purpose: its coordinates are three arbitrary
    strings, so guessing wastes requests and resolves nothing.
    """
    assert "workday" not in GUESSABLE
    assert "greenhouse" in GUESSABLE


def test_clean_strips_markup_and_entities():
    assert clean("<p>Hi&nbsp;&amp; bye</p>") == "Hi & bye"
    assert clean(None) == ""
