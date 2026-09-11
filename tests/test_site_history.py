"""
Tests for how the history layer renders.

These blocks cannot be exercised by building the live site, because the gates
correctly hold them shut: one measured day produces no closed postings and so
no median, and a single data point is not a chart. Zero of 1,242 pages render
either block today, which is right and also means neither has ever been looked
at by anything but a human reading the template.

So both halves are pinned here - that the blocks appear with sufficient data,
and that they stay shut without it. The second half is the one that matters.
A page claiming "the typical role was open for 0 days" off a single day of
observation is the same failure as the 2600% hiring claim, just quieter.
"""

import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "site"))

import build as B  # noqa: E402


@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(ROOT / "site" / "templates"),
                    autoescape=select_autoescape(["html"]))
    e.filters["fmt"] = lambda v: f"{int(v):,}" if v is not None else "—"
    return e


def _company_ctx(**over):
    ctx = {
        "c": {"company_name": "Acme", "total_postings": 500, "postings_last_30d": 40,
              "distinct_states": 5, "total_filings": None, "years_filing": None,
              "certified_pct": None, "weighted_median_wage": None},
        "rel": "../../", "nav": "companies", "canonical": "/companies/acme/",
        "page_title": "t", "page_description": "d",
        "stats": {"postings": 65598, "companies": 1117, "total_filings": 0},
        "freshness": {}, "site_url": "", "repo_url": "",
        "generated_at": "11 Sep 2026", "coverage": {}, "history_days": 40,
        "trend_ok": True, "headline": None, "techs": [], "roles": [],
        "peers": [], "peer_stats": None, "market_pos": [], "pace": None,
    }
    ctx.update(over)
    return ctx


# --------------------------------------------------------- the company pace block

def test_pace_block_renders_with_a_real_median(env):
    html = env.get_template("company.html").render(**_company_ctx(pace={
        "postings_observed": 503, "median_days_open": 18.0, "closed_share": 0.42,
    }))
    assert "How long their roles stay open" in html
    assert "18" in html
    assert "42%" in html          # the share of the sample that has closed


def test_pace_block_is_hidden_without_a_median(env):
    """
    What today's data actually looks like: 110 companies observed, none with a
    closed posting yet, so median_days_open is NULL. Rendering "open for 0 days"
    there would be a false claim dressed as a measurement.
    """
    html = env.get_template("company.html").render(**_company_ctx(pace={
        "postings_observed": 503, "median_days_open": None, "closed_share": 0.0,
    }))
    assert "How long their roles stay open" not in html


def test_pace_block_is_hidden_with_no_history_at_all(env):
    html = env.get_template("company.html").render(**_company_ctx(pace=None))
    assert "How long their roles stay open" not in html


def test_pace_block_states_its_own_limits(env):
    """
    The note is not decoration. Someone reading a median has to know it counts
    only closed postings, or they will read it as time-to-fill for all roles.
    """
    html = env.get_template("company.html").render(**_company_ctx(pace={
        "postings_observed": 503, "median_days_open": 18.0, "closed_share": 0.42,
    }))
    assert "stopped appearing" in html
    assert "no end date yet" in html


# ------------------------------------------------------------- the sparkline

def test_sparkline_needs_at_least_two_points():
    one = [{"observed_date": "2026-09-11", "postings_mentioning": 5}]
    assert B.sparkline(one, "postings_mentioning") is None
    assert B.sparkline([], "postings_mentioning") is None


def test_sparkline_maps_the_series_onto_the_box():
    rows = [{"observed_date": f"2026-09-{d:02d}", "postings_mentioning": v}
            for d, v in zip(range(1, 5), [10, 20, 15, 30], strict=True)]
    sp = B.sparkline(rows, "postings_mentioning", width=300, height=100)
    xs = [float(p.split(",")[0]) for p in sp["points"].split()]
    ys = [float(p.split(",")[1]) for p in sp["points"].split()]
    assert xs == [0.0, 100.0, 200.0, 300.0]     # evenly spaced across the width
    assert min(ys) == 0.0 and max(ys) == 100.0  # the low sits at the bottom
    assert sp["first"] == 10 and sp["last"] == 30
    assert sp["low"] == 10 and sp["high"] == 30
    assert sp["days"] == 4


def test_sparkline_survives_a_flat_series():
    """A constant series must not divide by a zero span."""
    rows = [{"observed_date": f"2026-09-{d:02d}", "postings_mentioning": 7}
            for d in range(1, 4)]
    sp = B.sparkline(rows, "postings_mentioning")
    assert sp is not None
    assert sp["low"] == sp["high"] == 7


def _tech_ctx(**over):
    ctx = {
        "t": {"tech_name": "Python", "tech_slug": "python", "openings": 1000,
              "category": "language", "postings_mentioning": 900,
              "pct_top_band": None},
        "rel": "../../", "nav": "tech", "canonical": "/tech/python/",
        "page_title": "t", "page_description": "d",
        "stats": {"postings": 65598, "companies": 1117, "total_filings": 0},
        "freshness": {}, "site_url": "", "repo_url": "",
        "generated_at": "11 Sep 2026", "coverage": {}, "history_days": 40,
        "trend_ok": True, "employers": [], "pairs": [], "category_size": 10,
        "history": [], "spark": None,
    }
    ctx.update(over)
    return ctx


def test_chart_renders_and_discloses_its_axis(env):
    rows = [{"observed_date": f"2026-09-{d:02d}", "postings_mentioning": v}
            for d, v in zip(range(1, 5), [10, 20, 15, 30], strict=True)]
    html = env.get_template("tech.html").render(
        **_tech_ctx(spark=B.sparkline(rows, "postings_mentioning")))
    assert "Measured demand over time" in html
    assert "<polyline" in html
    # A truncated axis that does not say so is a misleading chart.
    assert "rather than zero" in html
    # And the real endpoints appear as text, not only as a shape.
    assert "10" in html and "30" in html


def test_chart_is_hidden_without_a_series(env):
    html = env.get_template("tech.html").render(**_tech_ctx(spark=None))
    assert "Measured demand over time" not in html
    assert "<polyline" not in html


# ------------------------------------------------------- the slug collision guard

def test_colliding_slugs_fail_the_build():
    """
    The real case: two spellings of Fivetran wanted /companies/fivetran/, and
    the 29-posting page overwrote the 177-posting one. Silently, for weeks.
    """
    with pytest.raises(SystemExit) as exc:
        B.assert_unique_slugs([
            {"slug": "fivetran", "company_name": "FiveTran"},
            {"slug": "fivetran", "company_name": "Fivetran"},
        ])
    assert "collision" in str(exc.value)


def test_unique_slugs_pass():
    B.assert_unique_slugs([
        {"slug": "fivetran", "company_name": "FiveTran"},
        {"slug": "databricks-inc", "company_name": "Databricks, Inc."},
    ])


def test_the_guard_reports_every_collision_not_just_the_first():
    with pytest.raises(SystemExit) as exc:
        B.assert_unique_slugs([
            {"slug": "a", "company_name": "A"}, {"slug": "a", "company_name": "A."},
            {"slug": "b", "company_name": "B"}, {"slug": "b", "company_name": "B."},
        ])
    assert "2 company slug collision" in str(exc.value)
