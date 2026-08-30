"""
Tests for the outreach message layer.

Every number in a message is interpolated from a SQL-derived record, so the
thing worth testing is that the templates cannot introduce a claim of their
own, and that the channel limits hold. These messages go to people who know
their own hiring data.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("outreach", "storage", "ai_layer"):
    sys.path.insert(0, str(ROOT / sub))

from insights import MIN_DAYS_FOR_TREND, Insight, build_insights  # noqa: E402


def _facts(**over):
    base = {"roles": 100, "last_30d": 40, "prior_30d": 20, "states": 8,
            "departments": 3, "countries": "us", "top_departments": [],
            "stack": [], "stack_shares": {}}
    base.update(over)
    return base


# ---------------------------------------------------- the pace-change gate

def test_pace_change_is_suppressed_without_collection_history():
    """
    The claim that would have opened a message to Google was "your posting
    pace is up 2600% versus the previous month".

    It is an artifact. Job boards delist filled roles, so a freshly collected
    corpus always holds more recent postings than older ones - corpus-wide the
    same effect reads as 1.9x. With a few days of history the percentage
    describes when collection started, not how a company hires.
    """
    ins = build_insights("Acme", _facts(last_30d=81, prior_30d=3), {}, {},
                         days_collected=4)
    assert not any(i.kind == "pace_change" for i in ins)


def test_pace_change_allowed_once_history_is_long_enough():
    ins = build_insights("Acme", _facts(last_30d=80, prior_30d=20), {}, {},
                         days_collected=MIN_DAYS_FOR_TREND)
    assert any(i.kind == "pace_change" for i in ins)


def test_pace_change_still_needs_a_thick_prior_month():
    """Even with history, three postings cannot support a percentage."""
    ins = build_insights("Acme", _facts(last_30d=81, prior_30d=3), {}, {},
                         days_collected=365)
    assert not any(i.kind == "pace_change" for i in ins)


# ------------------------------------------------- the company's own baseline

def test_company_is_excluded_from_the_baseline_it_is_measured_against():
    """
    A comparison containing one of its sides is not a comparison.

    Databricks read as 9.6x its baseline while inside it, and 16.2x once
    removed.
    """
    facts = _facts(roles=1000, stack=[("spark", 170)])
    peers = {"stack_counts": {"spark": 200.0}, "total_postings": 11000.0}
    market = {"spark": {"name": "Apache Spark", "rank_in_category": 5,
                        "category": "processing", "openings": 1}}
    ins = build_insights("Databricks", facts, peers, market, days_collected=4)
    emphasis = [i for i in ins if i.kind == "stack_emphasis"]
    assert emphasis, "expected a stack emphasis insight"
    # baseline = (200-170)/(11000-1000) = 0.003; own = 0.17 -> ~57x, not ~9x
    assert emphasis[0].evidence["peer_share"] == 0.003


# ------------------------------------------------------------ message shape

def _insight(text="they are hiring across 8 states"):
    return [Insight("company", "geography", text, {}, strength=50)]


def test_connection_note_respects_linkedins_limit():
    """LinkedIn truncates a connection note at 300 characters."""
    from compose import CONNECTION_LIMIT, connection_note
    long_fact = "they mention " + ("Apache Spark and " * 20) + "in most postings"
    note = connection_note("A Company With A Very Long Name Indeed", _insight(long_fact))
    assert len(note) <= CONNECTION_LIMIT


def test_messages_carry_the_company_page_link():
    from compose import company_url, email, followup
    for text in (followup("Databricks", _insight()), email("Databricks", _insight())):
        assert company_url("Databricks") in text


def test_possessive_handles_names_ending_in_s():
    from compose import possessive
    assert possessive("Databricks") == "Databricks'"
    assert possessive("Stripe") == "Stripe's"


def test_email_never_mentions_a_requisition_or_asks_for_time():
    """
    The ask is deliberately for an opinion, not a calendar slot, and a req
    number turns a message into a ticket.
    """
    from compose import email
    low = email("Stripe", _insight()).lower()
    assert "requisition" not in low and "req id" not in low
    assert "opinion" in low


def test_paragraphs_are_stored_unwrapped():
    """Wrapping is a display concern; stored text must survive being pasted."""
    from compose import followup
    text = followup("Stripe", _insight())
    for para in text.split("\n\n"):
        assert "\n" not in para
