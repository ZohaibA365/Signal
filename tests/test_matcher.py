"""
Tests for matching posting company names to DOL H-1B filers.

This is the highest-stakes inference in the project. Its output becomes a
sponsorship claim on a public page, read by someone deciding where to spend a
job application, and the candidate reading it needs sponsorship to work at all.
So the bar is precision, not coverage, and the tests are written against real
pairs observed in the live data rather than invented ones.

Two defects motivated them. The matcher ran in no workflow, so the mapping
froze on 28 August while board discovery kept adding employers - 1,226 of 3,821
companies had never been offered to it. And when a brand prefixed several legal
entities it took the lexicographically first: "Cognizant" resolved to COGNIZANT
MOBILITY with 13 filings rather than COGNIZANT TECHNOLOGY SOLUTIONS with
15,274.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("storage", "ingestion"):
    sys.path.insert(0, str(ROOT / sub))

import load_dol  # noqa: E402
from dol_ingest import normalise_employer  # noqa: E402


class FakeCursor:
    """Answers the two SELECTs build_mapping issues; records what it is given."""

    def __init__(self, companies, filings_by_key):
        self._companies = companies
        self._filings = filings_by_key
        self._next = None
        self.truncated = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if "FROM raw_postings" in flat:
            self._next = [(c,) for c in self._companies]
        elif "FROM dol_employer_summary" in flat:
            self._next = list(self._filings.items())
        elif flat.startswith("TRUNCATE"):
            self.truncated.append(flat.split()[-1])
            self._next = []
        else:
            self._next = []

    def fetchall(self):
        return self._next


def run(companies, filings_by_key, monkeypatch):
    """Run build_mapping against fakes and return (mapping, candidates)."""
    captured = {}

    def fake_execute_values(cur, sql, rows, page_size=None):
        key = "mapping" if "company_employer_key" in sql else "candidates"
        captured[key] = rows

    monkeypatch.setattr(load_dol, "execute_values", fake_execute_values)
    load_dol.build_mapping(FakeCursor(companies, filings_by_key))
    return ({m[0]: (m[1], m[2]) for m in captured.get("mapping", [])},
            captured.get("candidates", []))


# ------------------------------------------------------------ the staleness fix

def test_exact_match_on_the_normalised_name(monkeypatch):
    """
    "Databricks, Inc." normalises to DATABRICKS, which is in the filing data
    with 547 filings. It showed no sponsorship for two weeks only because the
    mapping was never rebuilt.
    """
    mapping, _ = run(["Databricks, Inc."], {"DATABRICKS": 547}, monkeypatch)
    assert mapping["Databricks, Inc."] == ("DATABRICKS", "exact")


def test_the_mapping_is_keyed_on_the_trimmed_name():
    """
    stg_jobs.sql emits nullif(trim(company_name),'') and every model joins on
    that, so an untrimmed key is one nothing downstream can ever match. 16 rows
    carried one.

    Asserted against the SQL rather than through a fake cursor, because the trim
    happens in Postgres - simulating it here would only test the simulation.
    """
    import inspect
    src = inspect.getsource(load_dol.build_mapping)
    assert "trim(company_name)" in src


# ------------------------------------------------------- the ambiguity fix

def test_cognizant_is_left_undecided_not_guessed(monkeypatch):
    """
    The regression. Four real candidates; the old code took the lexicographic
    first, which is the one with 13 filings out of 15,355.
    """
    mapping, candidates = run(["Cognizant"], {
        "COGNIZANT TECHNOLOGY SOLUTIONS": 15274,
        "COGNIZANT TRIZETTO SOFTWARE GROUP": 42,
        "COGNIZANT WORLDWIDE": 26,
        "COGNIZANT MOBILITY": 13,
    }, monkeypatch)
    assert mapping["Cognizant"] == (None, "prefix_ambiguous")
    assert len(candidates) == 4


def test_filing_volume_never_decides(monkeypatch):
    """
    The obvious fix for ambiguity - take the candidate with the most filings -
    is also wrong. "Lucid Motors" prefixes LUCID, which has 842 filings and is
    a different company; Lucid Motors files as LUCID GROUP with 15.
    """
    mapping, _ = run(["Lucid Motors"], {"LUCID": 842, "LUCID GROUP": 15},
                     monkeypatch)
    assert mapping["Lucid Motors"][0] is None


def test_a_single_multiword_prefix_is_still_trusted(monkeypatch):
    """Capital One -> CAPITAL ONE SERVICES remains safe and is the whole point."""
    mapping, _ = run(["Capital One"], {"CAPITAL ONE SERVICES": 1029}, monkeypatch)
    assert mapping["Capital One"] == ("CAPITAL ONE SERVICES", "prefix_strong")


def test_a_single_generic_word_is_never_trusted(monkeypatch):
    """
    Sampling found Lighthouse -> LIGHTHOUSE BEHAVIORAL SOLUTIONS and
    Invictus -> INVICTUS ACADEMY OF RICHMOND: different organisations.
    """
    mapping, _ = run(["Lighthouse"], {"LIGHTHOUSE BEHAVIORAL SOLUTIONS": 4},
                     monkeypatch)
    assert mapping["Lighthouse"][1] == "prefix_weak"


def test_short_brands_do_not_prefix_match(monkeypatch):
    """
    The >=8 character floor. Without it "APPLE" matches "APPLE MOVERS" and a
    false sponsorship claim reaches a stranger. It also costs real recall -
    Oracle and TD Bank are below the floor - which is a deliberate trade.
    """
    mapping, _ = run(["Apple"], {"APPLE MOVERS": 2}, monkeypatch)
    assert mapping["Apple"] == (None, None)


def test_genuine_absence_stays_absent(monkeypatch):
    """
    SpaceX and Anduril are the two largest unmatched companies and neither
    appears in the filing data under any name - both are US-persons-only by
    export control. A matcher aggressive enough to "fix" them would tell a
    Canadian student to apply to SpaceX for sponsorship.
    """
    mapping, _ = run(["SpaceX", "Anduril Industries"],
                     {"GULFSTREAM AEROSPACE": 900, "ANDROID INDUSTRIES": 89},
                     monkeypatch)
    assert mapping["SpaceX"] == (None, None)
    assert mapping["Anduril Industries"] == (None, None)


def test_ambiguous_rows_carry_no_employer_key(monkeypatch):
    """
    The downstream gate gives sponsorship to exact and prefix_strong only, but
    defence in depth: an ambiguous row must not carry a key at all, so even a
    mistaken gate cannot publish one.
    """
    mapping, _ = run(["Cognizant"], {"COGNIZANT A": 5, "COGNIZANT B": 9},
                     monkeypatch)
    assert mapping["Cognizant"][0] is None


# ------------------------------------------------------------- the normaliser

@pytest.mark.parametrize("raw,expected", [
    ("Databricks, Inc.", "DATABRICKS"),
    ("AbbVie Inc.", "ABBVIE"),
    ("Waymo LLC", "WAYMO"),
    ("CoreWeave, Inc.", "COREWEAVE"),
    ("Snowflake Inc.", "SNOWFLAKE"),
    ("Oracle Corporation", "ORACLE"),
])
def test_normaliser_collapses_legal_suffixes(raw, expected):
    assert normalise_employer(raw) == expected


def test_normaliser_is_the_same_function_on_both_sides():
    """
    The join is exact-on-normalised, so one normaliser must serve both the
    posting names and the DOL names. Two normalisers would mean the join works
    only where they happen to agree.
    """
    assert normalise_employer("Stripe, Inc.") == normalise_employer("STRIPE INC")
