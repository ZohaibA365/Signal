"""
Tests for the sponsorship verdicts, which are now computed once at load time
and stored, rather than recomputed by every dbt run.

Two separate risks. The patterns themselves decide whether a real posting is
published as sponsoring, so they are checked against the phrasings that matter,
including one spanning a line break - the case that silently returned nothing on
Snowflake. And the CI fixture carries a generated copy of the same SQL, because
CI never runs the loader; if that copy drifts, CI stops exercising the verdicts
while still passing.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storage"))

from sponsorship_text import OFFERS, REFUSES, fixture_update, offers_sql, refuses_sql

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "seed_ci.sql"


def matches(patterns, text):
    """Approximates Postgres ~* closely enough for these patterns.

    Every pattern uses only alternation and a negated class, and a negated class
    matches a newline on both engines, so no flag juggling is needed here.
    """
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


class TestRefusals:
    def test_plain_refusal(self):
        assert matches(REFUSES, "We are unable to offer sponsorship for this role.")

    def test_refusal_below_a_line_break(self):
        """The Snowflake bug: everything after the first newline stopped matching."""
        assert matches(REFUSES, "About the team.\nWe cannot sponsor visas.")

    def test_authorisation_phrasing(self):
        assert matches(
            REFUSES,
            "Applicants must be authorized to work in the US and does not now or "
            "in the future require sponsorship.",
        )

    def test_an_offer_is_not_a_refusal(self):
        assert not matches(REFUSES, "Visa sponsorship is available for this position.")

    def test_a_sentence_boundary_stops_the_match(self):
        """[^.]{0,80} is what keeps two unrelated sentences from joining up."""
        assert not matches(
            REFUSES, "We do not require a degree. We file H-1B sponsorship yearly."
        )


class TestOffers:
    def test_available_phrasing(self):
        assert matches(OFFERS, "Visa sponsorship is available for this position.")

    def test_willingness_phrasing(self):
        assert matches(OFFERS, "We are willing to sponsor the right candidate.")

    def test_refusal_is_not_an_offer(self):
        assert not matches(OFFERS, "We are unable to offer sponsorship.")

    def test_a_bare_mention_is_not_an_offer(self):
        """Deliberately narrow: naming sponsorship is not promising it."""
        assert not matches(OFFERS, "Questions about sponsorship? Ask the recruiter.")


class TestSql:
    def test_patterns_are_dollar_quoted(self):
        """A pattern holds backslashes and braces; quoting it any other way breaks."""
        sql = refuses_sql()
        assert "$q$" in sql
        assert "'" not in sql

    def test_column_is_substituted_everywhere(self):
        sql = refuses_sql("EXCLUDED.description_raw")
        assert sql.count("EXCLUDED.description_raw") == len(REFUSES)

    def test_offers_and_refusals_are_separate_predicates(self):
        assert refuses_sql() != offers_sql()


class TestFixtureStaysInStep:
    def test_fixture_carries_the_generated_block(self):
        text = FIXTURE.read_text()
        assert fixture_update() in text, (
            "tests/fixtures/seed_ci.sql is out of step with the patterns. "
            "Regenerate it with: python storage/sponsorship_text.py"
        )

    def test_fixture_exercises_both_verdicts(self):
        """A fixture where nothing matches would pass CI and check nothing."""
        text = FIXTURE.read_text()
        descriptions = re.findall(r"E?'((?:[^']|'')*)'", text)
        unescaped = [d.replace("\\n", "\n") for d in descriptions]
        assert any(matches(REFUSES, d) for d in unescaped)
        assert any(matches(OFFERS, d) for d in unescaped)
