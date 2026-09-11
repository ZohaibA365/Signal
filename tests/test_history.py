"""
Tests for the history layer built over the S3 archive.

The thing worth testing here is not the SQL arithmetic, it is the honesty gate.
This project has already published one false trend claim - "posting pace up
2600%" computed across three collection days, which was the strongest insight
in a draft cold email to Google. The artifact came from dividing two windows
inside a corpus younger than the windows.

The archive reintroduces the same hazard in a new shape. A backfill recovers
only first_seen and last_seen, so a posting open from 23 August to 9 September
contributes two rows and nothing between: measured across the corpus that is
1.84 observed days against a 17-day span. Counting "roles open on the 27th"
over that panel undercounts by an unknown amount, and an undercount in a time
series reads as a decline rather than as missing data.

So coverage is counted from archive run markers, not from dates present in the
panel, and every rate is gated on it.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "storage"))
sys.path.insert(0, str(ROOT / "analytics"))

import build_history as hist  # noqa: E402
import duckdb  # noqa: E402


def _db(markers, presence):
    """An in-memory stand-in for the archive views build_history queries."""
    d = duckdb.connect()
    d.execute("CREATE TABLE runs (observed_date DATE, run_mode VARCHAR)")
    d.execute("CREATE TABLE presence (source VARCHAR, job_id VARCHAR, "
              "observed_date DATE, observed_via VARCHAR)")
    d.execute("CREATE TABLE postings (source VARCHAR, job_id VARCHAR, "
              "country VARCHAR, company_name VARCHAR)")
    for day, mode in markers:
        d.execute("INSERT INTO runs VALUES (?, ?)", [day, mode])
    for src, jid, day, via in presence:
        d.execute("INSERT INTO presence VALUES (?, ?, ?, ?)", [src, jid, day, via])
        d.execute("INSERT INTO postings SELECT ?, ?, 'us', 'Acme' "
                  "WHERE NOT EXISTS (SELECT 1 FROM postings WHERE source=? AND job_id=?)",
                  [src, jid, src, jid])
    return d


# ----------------------------------------------------- the coverage distinction

def test_panel_days_are_not_counted_as_coverage():
    """
    Nine dates exist in the panel and none of them was covered by a daily run.
    Coverage must read zero. Counting panel dates is precisely the mistake that
    would let a backfill's two-sightings-per-posting masquerade as history.
    """
    presence = [("board", f"j{i}", f"2026-08-{23 + i:02d}", "first_seen")
                for i in range(9)]
    cov = hist.coverage(_db(markers=[], presence=presence))
    assert cov["panel_days"] == 9
    assert cov["measured_days"] == 0
    assert cov["trend_is_publishable"] is False


def test_only_daily_markers_count():
    """A backfill marker, if one ever appeared, must not grant coverage."""
    cov = hist.coverage(_db(
        markers=[("2026-09-01", "backfill"), ("2026-09-02", "daily")],
        presence=[("board", "j1", "2026-09-02", "last_seen")]))
    assert cov["measured_days"] == 1


def test_gate_opens_only_at_the_threshold():
    days = [f"2026-09-{d:02d}" for d in range(1, hist.MIN_MEASURED_DAYS + 1)]
    one_short = hist.coverage(_db([(d, "daily") for d in days[:-1]],
                                  [("board", "j1", days[0], "last_seen")]))
    exact = hist.coverage(_db([(d, "daily") for d in days],
                              [("board", "j1", days[0], "last_seen")]))
    assert one_short["measured_days"] == hist.MIN_MEASURED_DAYS - 1
    assert one_short["trend_is_publishable"] is False
    assert exact["trend_is_publishable"] is True


def test_threshold_is_not_accidentally_tiny():
    """
    A low threshold is how the 2600% claim got published. Four weeks of complete
    daily coverage is the floor, and it must be a deliberate number.
    """
    assert hist.MIN_MEASURED_DAYS >= 28


# ------------------------------------------------- the panel excludes bad days

def test_daily_panel_skips_uncovered_days():
    """
    An undercounted day is worse than an absent one: a gap is visible in a
    chart, a partial count looks like roles disappearing.
    """
    d = _db(markers=[("2026-09-02", "daily")], presence=[
        ("board", "j1", "2026-09-01", "first_seen"),   # no marker - excluded
        ("board", "j1", "2026-09-02", "last_seen"),
        ("board", "j2", "2026-09-02", "last_seen"),
    ])
    rows = hist.daily_roles(d)
    assert [r[0].isoformat() for r in rows] == ["2026-09-02"]
    assert rows[0][2] == 2          # both postings open that day


def test_tech_history_also_respects_coverage(monkeypatch):
    """The same gate, on the model that replaces a survivorship-biased one."""
    d = _db(markers=[("2026-09-02", "daily")], presence=[
        ("board", "j1", "2026-09-01", "first_seen"),
        ("board", "j1", "2026-09-02", "last_seen"),
    ])

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, _q): pass
        def fetchall(self): return [("board", "j1", "python")]

    class FakeConn:
        def cursor(self): return FakeCursor()

    rows = hist.tech_daily(d, FakeConn())
    assert len(rows) == 1
    assert rows[0][0].isoformat() == "2026-09-02"
    assert rows[0][1] == "python"


# ------------------------------------------------------------ closure honesty

def test_thin_samples_are_not_published():
    """Two postings is not a hiring pace. The per-company floor excludes them."""
    d = _db(markers=[("2026-09-05", "daily")], presence=[
        ("board", "a", "2026-09-01", "first_seen"),
        ("board", "b", "2026-09-01", "first_seen"),
    ])
    assert hist.company_pace(d) == []


def test_median_days_open_uses_only_closed_postings():
    """
    A posting still appearing has no end date yet, and one that predates
    collection looks younger than it is. Including either in a "time to fill"
    median understates it. So only postings that stopped appearing count, and
    closed_share reports how much of the sample that median rests on.

    Here eight postings clear the floor: four closed after 2 days, four still
    open on the latest day. The median must be 2, not something pulled down by
    the open ones, and closed_share must be 0.5.
    """
    presence = []
    for i in range(4):          # closed: last seen before the panel's latest day
        presence += [("board", f"c{i}", "2026-09-01", "first_seen"),
                     ("board", f"c{i}", "2026-09-03", "last_seen")]
    for i in range(4):          # still open on the latest day
        presence += [("board", f"o{i}", "2026-09-04", "first_seen"),
                     ("board", f"o{i}", "2026-09-05", "last_seen")]
    rows = hist.company_pace(_db(markers=[("2026-09-05", "daily")], presence=presence))

    assert len(rows) == 1
    (_company, _country, postings, _first, _last,
     _open_days, median_days_open, closed_share) = rows[0]
    assert postings == 8
    assert float(median_days_open) == 2.0     # the closed ones only
    assert float(closed_share) == 0.5         # and half the sample is closed


@pytest.mark.parametrize("column", ["measured_days", "panel_days",
                                    "trend_is_publishable",
                                    "min_measured_days_required"])
def test_coverage_reports_everything_the_site_needs_to_decide(column):
    cov = hist.coverage(_db([], [("board", "j1", "2026-09-01", "last_seen")]))
    assert column in cov
