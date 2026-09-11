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
    """
    Run build_mapping against fakes and return (mapping, candidates).

    The real alias file is stubbed out. These tests exercise the mechanical
    rules against tiny fake filing sets, and the loader fails loudly when an
    alias names a key that does not exist - which every shipped alias does, in
    a fake set of three keys. Aliases have their own tests further down.
    """
    captured = {}

    def fake_execute_values(cur, sql, rows, page_size=None):
        if "company_employer_link" in sql:
            captured["links"] = rows
        elif "company_employer_key" in sql:
            captured["mapping"] = rows
        else:
            captured["candidates"] = rows

    monkeypatch.setattr(load_dol, "execute_values", fake_execute_values)
    monkeypatch.setattr(load_dol, "load_aliases", lambda: ({}, set()))
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
    assert "trim(r.company_name)" in src
    # And on the canonical name, or the 184 companies that company_identity
    # merged would have a mapping key nothing downstream joins to.
    assert "canonical_name" in src


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


# ------------------------------------------------- canonicalising company names
#
# 33 employers were published as two pages each, and three slugs collided
# outright: /companies/fivetran/ was written for "FiveTran" with 177 postings
# and then overwritten by "Fivetran" with 29, so the published page understated
# that employer six-fold and sitemap.xml listed the URL twice.

from resolve_companies import resolve  # noqa: E402


def test_suffix_variants_collapse_to_one_employer():
    rows = [("Databricks, Inc.", 494), ("Databricks", 90)]
    out = {name: canonical for name, _key, canonical in resolve(rows)}
    assert out["Databricks"] == "Databricks, Inc."
    assert out["Databricks, Inc."] == "Databricks, Inc."


def test_the_larger_page_keeps_its_url():
    """
    Canonical is the variant with the most postings, because that is the page
    more likely to be indexed. Prettiness of the URL is not the criterion.
    """
    rows = [("Oracle", 1256), ("Oracle Corporation", 5)]
    out = {n: c for n, _k, c in resolve(rows)}
    assert set(out.values()) == {"Oracle"}


def test_the_fivetran_collision():
    """The case that was silently losing 148 postings from a published page."""
    rows = [("FiveTran", 177), ("Fivetran", 29)]
    out = {n: c for n, _k, c in resolve(rows)}
    assert set(out.values()) == {"FiveTran"}


def test_ties_break_deterministically():
    """
    Equal postings must not let row order decide, or the canonical name flips
    between runs and every URL churns.
    """
    a = resolve([("Acme Inc.", 10), ("Acme", 10)])
    b = resolve([("Acme", 10), ("Acme Inc.", 10)])
    assert {n: c for n, _k, c in a} == {n: c for n, _k, c in b}
    # Shortest name wins the tie.
    assert {c for _n, _k, c in a} == {"Acme"}


def test_different_companies_are_not_merged():
    """
    The whole risk of canonicalisation. These share a token and nothing else,
    and must stay separate.
    """
    rows = [("Snap Inc.", 100), ("SnapLogic", 50),
            ("Lucid Motors", 40), ("Lucid Software", 30)]
    out = {n: c for n, _k, c in rows and resolve(rows)}
    assert out["Snap Inc."] != out["SnapLogic"]
    assert out["Lucid Motors"] != out["Lucid Software"]


def test_blank_and_unnameable_rows_are_dropped():
    assert resolve([("", 5), ("   ", 5), ("Inc.", 5)]) == []


def test_every_input_name_appears_in_the_output():
    """
    stg_jobs left-joins this mapping, so a name missing from it silently falls
    back to its raw spelling - which is the bug, not the fallback.
    """
    rows = [("Stripe, Inc.", 50), ("Stripe", 10), ("Figma, Inc.", 70)]
    assert {n for n, _k, _c in resolve(rows)} == {r[0] for r in rows}


# ------------------------------------------------------- hand-written aliases
#
# The cheapest high-precision recall in the project: 31 lines of CSV added nine
# percentage points of posting coverage, from 50.7% to 59.7%. The automated
# rules cannot reach these at all - Esri files as ENVIRONMENTAL SYSTEMS RESEARCH
# INSTITUTE ESRI - and brands shorter than the eight-character prefix floor
# (Oracle, TD Bank, BMO) are invisible to it by design.


def run_links(companies, filings_by_key, monkeypatch, aliases=None):
    """As run(), but returns the one-to-many entity links as well."""
    captured = {}

    def fake_execute_values(cur, sql, rows, page_size=None):
        if "company_employer_link" in sql:
            captured["links"] = rows
        elif "company_employer_key" in sql:
            captured["mapping"] = rows
        else:
            captured["candidates"] = rows

    monkeypatch.setattr(load_dol, "execute_values", fake_execute_values)
    if aliases is not None:
        monkeypatch.setattr(load_dol, "load_aliases", lambda: aliases)
    load_dol.build_mapping(FakeCursor(companies, filings_by_key))
    return ({m[0]: (m[1], m[2]) for m in captured.get("mapping", [])},
            captured.get("links", []))


def test_one_company_can_own_several_entities(monkeypatch):
    """
    Capital One files as two legal entities. Reading one key reported 1,029
    filings instead of 1,552; PwC 201 instead of 1,778; Cognizant 15,274
    instead of 15,355.
    """
    aliases = ({"Capital One": ["CAPITAL ONE SERVICES",
                                "CAPITAL ONE NATIONAL ASSOCIATION"]}, set())
    _mapping, links = run_links(
        ["Capital One"],
        {"CAPITAL ONE SERVICES": 1029, "CAPITAL ONE NATIONAL ASSOCIATION": 523},
        monkeypatch, aliases)
    assert sorted(k for _c, k, _t in links) == ["CAPITAL ONE NATIONAL ASSOCIATION",
                                                "CAPITAL ONE SERVICES"]
    assert {t for _c, _k, t in links} == {"alias_seed"}


def test_a_rejected_alias_is_never_linked(monkeypatch):
    """
    Accenture Federal Services is the cleared-federal entity and is not the
    Accenture with 4,055 filings. A rejection has to be as durable as an accept,
    or the pair comes back for review every run.
    """
    aliases = ({}, {("Accenture Federal Services", "ACCENTURE")})
    _mapping, links = run_links(["Accenture Federal Services"],
                                {"ACCENTURE": 4055}, monkeypatch, aliases)
    assert links == []


def test_rejected_pairs_are_dropped_from_the_review_queue(monkeypatch):
    """A decision made once should not be offered again."""
    captured = {}

    def fake_execute_values(cur, sql, rows, page_size=None):
        if "candidates" in sql:
            captured["candidates"] = rows

    monkeypatch.setattr(load_dol, "execute_values", fake_execute_values)
    monkeypatch.setattr(load_dol, "load_aliases",
                        lambda: ({}, {("Lucid Motors", "LUCID SOFTWARE")}))
    load_dol.build_mapping(FakeCursor(
        ["Lucid Motors"], {"LUCID MOTORS GROUP": 15, "LUCID SOFTWARE": 22}))
    keys = {k for _c, k, _f in captured.get("candidates", [])}
    assert "LUCID SOFTWARE" not in keys


def test_only_trusted_match_types_become_links(monkeypatch):
    """
    prefix_weak and prefix_ambiguous must never reach the link table, because
    that table is what sponsorship totals are summed over. Defence in depth: the
    dbt gate also filters, but a row that never exists cannot be published by a
    mistaken gate.
    """
    _mapping, links = run_links(
        ["Lighthouse", "Cognizant"],
        {"LIGHTHOUSE BEHAVIORAL SOLUTIONS": 4,
         "COGNIZANT A": 5, "COGNIZANT B": 9},
        monkeypatch, ({}, set()))
    assert links == []


def test_a_typo_in_the_alias_file_fails_loudly(monkeypatch):
    """
    An alias naming a key that does not exist would silently remove evidence
    rather than add it, so it stops the run instead.
    """
    aliases = ({"Oracle": ["ORACLE AMERCIA"]}, set())   # deliberate typo
    with pytest.raises(SystemExit) as exc:
        run_links(["Oracle"], {"ORACLE AMERICA": 1599}, monkeypatch, aliases)
    assert "ORACLE AMERCIA" in str(exc.value)


def test_the_shipped_alias_file_parses_and_is_internally_consistent():
    """
    The committed file itself, not a fixture. Every row needs a verdict the
    loader understands, and no pair may be both accepted and rejected.
    """
    accepts, rejects = load_dol.load_aliases()
    assert accepts, "the shipped alias file should not be empty"
    pairs = {(c, k) for c, ks in accepts.items() for k in ks}
    assert not (pairs & rejects), "a pair is both accepted and rejected"
