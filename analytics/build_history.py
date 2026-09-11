"""
Posting history, computed over the S3 archive with DuckDB.

Why this exists, and why it is not in the warehouse. `raw_postings` stores one
`last_seen` per posting and the loader overwrites it for all ~54,000 postings
every morning, so "this posting was open on the 9th" survives for one day.
`prune.py` deletes rows outright. The question "how many roles was this company
holding open each week" is therefore unanswerable from Neon by construction,
not by omission - and it is the question behind every trend claim on the site.

Why DuckDB rather than a warehouse. The archive is already Parquet in S3, which
DuckDB queries in place over httpfs - no load step, no cluster, no bill, and it
runs inside the daily pipeline like any other script. Snowflake was the original
plan and the trial expired; the work it was going to do is here instead, and
the models stay plain SQL so nothing is welded to DuckDB either.

The output is deliberately tiny and goes back into Neon. The site reads only
Neon, so it gains trend charts without a new dependency, and those charts keep
working whatever happens to the engine that computed them.

## The coverage problem, which is the whole reason this is careful

A backfill can only recover `first_seen` and `last_seen`, so a posting open
from 23 August to 9 September contributes two rows and nothing in between.
Measured over the corpus that is 1.84 observed days against a 17-day span. Any
"roles open on the 27th" computed over that panel undercounts by an unknown
amount, and dividing two such numbers produces a trend that is an artifact of
when collection started.

That mistake has already been published once by this project - "posting pace up
2600%" on three days of data, which nearly opened a cold email to Google. So:

  - only dates with an `archive/runs/` marker of mode `daily` count as complete
  - every rate or trend is gated on MIN_MEASURED_DAYS of those
  - `hist_coverage` states the depth, and is the single authoritative answer to
    "how much history is there", replacing the two divergent MIN_DAYS_FOR_TREND
    constants in site/build.py and outreach/insights.py

Until the gate opens this writes coverage and the daily panel and suppresses
everything else. That is the correct output, not a failure.

Usage:
    python analytics/build_history.py                 # compute and write back
    python analytics/build_history.py --dry-run       # compute and print only
    python analytics/build_history.py --show          # print the panel too
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import duckdb
from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
from db import connect, describe  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("history")

# Days of genuinely complete coverage before any rate or trend is published.
# 28 rather than the 45 the old constants used: 45 was picked to be safely past
# the artifact, but the artifact came from dividing two windows inside a
# three-day corpus. With complete daily coverage four weeks is enough to say
# "roles opened per week" honestly, and the gate is on MEASURED days, which the
# old constants never checked at all.
MIN_MEASURED_DAYS = 28

# Companies carried in the write-back. The tail is long and mostly noise at one
# or two postings; the site only renders pages for companies above its own
# floor anyway.
MIN_POSTINGS_PER_COMPANY = 8


def duck(bucket: str) -> duckdb.DuckDBPyConnection:
    """DuckDB with S3 access, reading the archive in place rather than loading it."""
    d = duckdb.connect()
    d.execute("INSTALL httpfs; LOAD httpfs;")
    d.execute("SET s3_region=?", [os.getenv("AWS_REGION", "us-east-1")])
    d.execute("SET s3_access_key_id=?", [os.environ["AWS_ACCESS_KEY_ID"]])
    d.execute("SET s3_secret_access_key=?", [os.environ["AWS_SECRET_ACCESS_KEY"]])
    d.execute(f"""
        CREATE VIEW presence AS
        SELECT * FROM read_parquet('s3://{bucket}/archive/presence/*/*.parquet',
                                   hive_partitioning = 1);
        CREATE VIEW postings AS
        SELECT * FROM read_parquet('s3://{bucket}/archive/postings/*/*.parquet',
                                   hive_partitioning = 1);
    """)
    # The run markers may not exist yet - the archive predates them - so an
    # empty set has to be a valid answer rather than a missing-file error.
    try:
        d.execute(f"""
            CREATE VIEW runs AS
            SELECT * FROM read_parquet('s3://{bucket}/archive/runs/*/*.parquet',
                                       hive_partitioning = 1);
        """)
    except duckdb.Error:
        log.warning("no archive/runs markers yet - treating every date as incomplete")
        d.execute("CREATE TABLE runs (observed_date DATE, run_mode VARCHAR)")
    return d


def coverage(d: duckdb.DuckDBPyConnection) -> dict:
    """
    How much history exists, split into what was measured and what was inferred.

    This is the number everything else is gated on, and the one the site should
    read. site/build.py counts distinct market_snapshots.snapshot_date and
    outreach/insights.py counts distinct raw_postings.first_seen::date - two
    different quantities, both called MIN_DAYS_FOR_TREND, neither of which
    measures whether a day's posting panel is actually complete.
    """
    row = d.execute("""
        WITH measured AS (
            SELECT DISTINCT observed_date FROM runs WHERE run_mode = 'daily'
        ), panel AS (
            SELECT DISTINCT observed_date FROM presence
        )
        SELECT (SELECT count(*) FROM measured)                       AS measured_days,
               (SELECT count(*) FROM panel)                          AS panel_days,
               (SELECT min(observed_date) FROM measured)              AS first_measured,
               (SELECT max(observed_date) FROM measured)              AS last_measured,
               (SELECT min(observed_date) FROM panel)                 AS first_panel,
               (SELECT max(observed_date) FROM panel)                 AS last_panel
    """).fetchone()
    cov = dict(zip(["measured_days", "panel_days", "first_measured", "last_measured",
                    "first_panel", "last_panel"], row, strict=True))
    cov["trend_is_publishable"] = cov["measured_days"] >= MIN_MEASURED_DAYS
    cov["min_measured_days_required"] = MIN_MEASURED_DAYS
    return cov


def daily_roles(d: duckdb.DuckDBPyConnection) -> list[tuple]:
    """
    Roles observed open per day and country - the panel Neon cannot hold.

    Restricted to measured days. An incomplete day in a time series is worse
    than a missing one: a gap is visible, an undercount looks like a decline.
    """
    return d.execute("""
        SELECT p.observed_date,
               coalesce(a.country, 'unknown')            AS country,
               count(DISTINCT p.source || ':' || p.job_id) AS open_roles,
               count(DISTINCT a.company_name)             AS companies
        FROM presence p
        JOIN runs r ON r.observed_date = p.observed_date AND r.run_mode = 'daily'
        LEFT JOIN postings a ON a.source = p.source AND a.job_id = p.job_id
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).fetchall()


def company_pace(d: duckdb.DuckDBPyConnection) -> list[tuple]:
    """
    Per company: how long its roles stay open, and how many it opens per week.

    `days_open` is observed span, not true lifetime - a posting open before
    collection began looks younger than it is, and one still open today has no
    end yet. `is_closed` is therefore the honest basis for any "time to fill"
    claim, and `closed_share` says how much of the sample supports it.
    """
    return d.execute(f"""
        -- Measured days only, like the other two models. An earlier version of
        -- this function computed lifespans over the whole panel, which was the
        -- exact mistake the coverage gate exists to prevent: with a backfill
        -- contributing two sightings per posting, median_days_open is
        -- meaningless and closed_share reads near 1.0 because almost every
        -- posting's last sighting predates the panel's most recent date. It
        -- reported 903 companies of confident nonsense. Restricting the source
        -- here makes the model honest by construction rather than relying on a
        -- caller to remember the check.
        WITH covered AS (
            SELECT p.*
            FROM presence p
            JOIN runs r ON r.observed_date = p.observed_date AND r.run_mode = 'daily'
        ), lifespan AS (
            SELECT p.source, p.job_id,
                   min(p.observed_date)                  AS first_observed,
                   max(p.observed_date)                  AS last_observed,
                   count(DISTINCT p.observed_date)       AS days_observed
            FROM covered p GROUP BY 1, 2
        ), latest AS (
            SELECT max(observed_date) AS latest_day FROM covered
        ), joined AS (
            SELECT a.company_name, a.country, l.*,
                   date_diff('day', l.first_observed, l.last_observed) AS days_open,
                   -- Not seen on the most recent day in the panel, so it has
                   -- stopped appearing on its board.
                   l.last_observed < (SELECT latest_day FROM latest) AS is_closed
            FROM lifespan l
            JOIN postings a ON a.source = l.source AND a.job_id = l.job_id
            WHERE a.company_name IS NOT NULL AND a.company_name <> ''
        )
        SELECT company_name,
               any_value(country)                            AS country,
               count(*)                                      AS postings_observed,
               min(first_observed)                           AS first_observed,
               max(last_observed)                            AS last_observed,
               count(DISTINCT first_observed)                AS distinct_open_days,
               median(days_open) FILTER (WHERE is_closed)    AS median_days_open,
               round(avg(CASE WHEN is_closed THEN 1 ELSE 0 END), 3) AS closed_share
        FROM joined
        GROUP BY 1
        HAVING count(*) >= {MIN_POSTINGS_PER_COMPANY}
        ORDER BY postings_observed DESC
    """).fetchall()


def tech_daily(d: duckdb.DuckDBPyConnection, conn) -> list[tuple]:
    """
    Technology mentions per measured day.

    The unbiased counterpart to tech_demand_history, whose own header admits it
    is survivorship biased: it reconstructs months from posted_date over
    postings that still exist, so a month's count reflects what survived to be
    collected rather than what was open. This counts what was actually observed
    open on each day.

    posting_technologies lives in Neon rather than the archive, so it is pulled
    across and registered. At ~85,000 narrow rows that is a few MB.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT source, job_id, tech_slug FROM posting_technologies")
        rows = cur.fetchall()
    if not rows:
        return []
    d.execute("CREATE OR REPLACE TABLE tech (source VARCHAR, job_id VARCHAR, "
              "tech_slug VARCHAR)")
    d.executemany("INSERT INTO tech VALUES (?, ?, ?)", rows)
    return d.execute("""
        SELECT p.observed_date, t.tech_slug,
               count(DISTINCT p.source || ':' || p.job_id) AS postings_mentioning
        FROM presence p
        JOIN runs r ON r.observed_date = p.observed_date AND r.run_mode = 'daily'
        JOIN tech t ON t.source = p.source AND t.job_id = p.job_id
        GROUP BY 1, 2
        ORDER BY 1, 3 DESC
    """).fetchall()


def write_back(conn, cov: dict, panel: list, pace: list, tech: list) -> None:
    """Upsert the summaries into Neon. A few thousand rows, well under 2 MB."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO hist_coverage (id, measured_days, panel_days, first_measured,
                                       last_measured, min_measured_days_required,
                                       trend_is_publishable, computed_at)
            VALUES (1, %(measured_days)s, %(panel_days)s, %(first_measured)s,
                    %(last_measured)s, %(min_measured_days_required)s,
                    %(trend_is_publishable)s, now())
            ON CONFLICT (id) DO UPDATE SET
                measured_days = EXCLUDED.measured_days,
                panel_days = EXCLUDED.panel_days,
                first_measured = EXCLUDED.first_measured,
                last_measured = EXCLUDED.last_measured,
                min_measured_days_required = EXCLUDED.min_measured_days_required,
                trend_is_publishable = EXCLUDED.trend_is_publishable,
                computed_at = now()
        """, cov)

        if panel:
            execute_values(cur, """
                INSERT INTO hist_daily_roles (observed_date, country, open_roles, companies)
                VALUES %s
                ON CONFLICT (observed_date, country) DO UPDATE SET
                    open_roles = EXCLUDED.open_roles,
                    companies  = EXCLUDED.companies
            """, panel, page_size=1000)

        if pace:
            execute_values(cur, """
                INSERT INTO hist_company_pace (company_name, country, postings_observed,
                    first_observed, last_observed, distinct_open_days,
                    median_days_open, closed_share)
                VALUES %s
                ON CONFLICT (company_name) DO UPDATE SET
                    country = EXCLUDED.country,
                    postings_observed = EXCLUDED.postings_observed,
                    first_observed = EXCLUDED.first_observed,
                    last_observed = EXCLUDED.last_observed,
                    distinct_open_days = EXCLUDED.distinct_open_days,
                    median_days_open = EXCLUDED.median_days_open,
                    closed_share = EXCLUDED.closed_share
            """, pace, page_size=1000)

        if tech:
            execute_values(cur, """
                INSERT INTO hist_tech_daily (observed_date, tech_slug, postings_mentioning)
                VALUES %s
                ON CONFLICT (observed_date, tech_slug) DO UPDATE SET
                    postings_mentioning = EXCLUDED.postings_mentioning
            """, tech, page_size=1000)
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build posting history from the S3 archive")
    ap.add_argument("--dry-run", action="store_true", help="compute but do not write to Neon")
    ap.add_argument("--show", action="store_true", help="print the daily panel")
    args = ap.parse_args()

    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise SystemExit("S3_BUCKET is not set - there is no archive to read")

    d = duck(bucket)
    conn = connect()
    try:
        cov = coverage(d)
        log.info("Coverage: %s measured day(s) of %s in the panel (%s to %s)",
                 cov["measured_days"], cov["panel_days"],
                 cov["first_measured"], cov["last_measured"])

        panel = daily_roles(d)
        pace = company_pace(d)
        tech = tech_daily(d, conn)
        log.info("  daily panel rows %s | companies %s | tech-day rows %s",
                 f"{len(panel):,}", f"{len(pace):,}", f"{len(tech):,}")

        if not cov["trend_is_publishable"]:
            log.warning("  trend output suppressed: %s measured days, need %s. "
                        "The panel and coverage are still written; rates are not "
                        "published until a full day's archive has run %s times.",
                        cov["measured_days"], MIN_MEASURED_DAYS, MIN_MEASURED_DAYS)

        if args.show:
            for row in panel:
                log.info("    %s  %-8s open=%-7s companies=%s", *row)

        if args.dry_run:
            log.info("dry run - nothing written")
            return

        write_back(conn, cov, panel, pace, tech)
        log.info("Wrote history summaries to %s", describe())
    finally:
        conn.close()
        d.close()


if __name__ == "__main__":
    main()
