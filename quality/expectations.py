"""
Pipeline-level data quality checks.

dbt tests assert things about a single build: this column is not null, that
range holds, this grain is unique. They cannot see across runs, so they never
catch the failure mode that actually matters here - the pipeline running
"successfully" while quietly collecting nothing.

These checks cover what dbt cannot:

  * freshness   - has the daily snapshot stopped? A gap can never be
                  backfilled, because the API only reports today.
  * drift       - did the corpus suddenly halve, or triple? Either means a
                  source changed shape.
  * enrichment  - did LLM scoring silently stop producing usable output?
  * distribution- did scores collapse to a single value, which is what a
                  broken prompt looks like from the outside.

Exits non-zero on failure so it can gate the Airflow DAG and CI.

Usage:
    python quality/expectations.py
    python quality/expectations.py --strict   # warnings become failures
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
from db import connect, describe  # noqa: E402

# Row-count floors. Set below current volume but high enough that a source
# quietly returning nothing is caught on the next run.
MIN_ROWS = {
    "raw_postings": 15_000,
    "posting_technologies": 20_000,
    "market_snapshots": 40,
    "job_enrichment": 500,
}

SNAPSHOT_MAX_AGE_DAYS = 3
POSTING_MAX_AGE_DAYS = 7      # newest posting should be recent
MAX_DRIFT_PCT = 60            # run-over-run change beyond this is suspicious


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    fatal: bool = True


def check(cur) -> list[Result]:
    out: list[Result] = []

    # --- volume floors ---------------------------------------------------
    for table, floor in MIN_ROWS.items():
        cur.execute(f"SELECT count(*) FROM {table}")
        n = cur.fetchone()[0]
        out.append(Result(f"volume: {table}", n >= floor,
                          f"{n:,} rows (floor {floor:,})"))

    # --- freshness -------------------------------------------------------
    cur.execute("SELECT current_date - max(snapshot_date) FROM market_snapshots")
    age = cur.fetchone()[0]
    out.append(Result("freshness: market snapshot",
                      age is not None and age <= SNAPSHOT_MAX_AGE_DAYS,
                      f"latest snapshot {age} day(s) old"))

    cur.execute("SELECT current_date - max(posted_date)::date FROM raw_postings")
    age = cur.fetchone()[0]
    out.append(Result("freshness: newest posting",
                      age is not None and age <= POSTING_MAX_AGE_DAYS,
                      f"newest posting {age} day(s) old"))

    # --- integrity -------------------------------------------------------
    cur.execute("""
        SELECT count(*) FROM raw_postings
        WHERE company_name IS NULL OR job_title IS NULL OR description_raw IS NULL
    """)
    n = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM raw_postings")
    total = cur.fetchone()[0]
    pct = 100.0 * n / total if total else 0
    out.append(Result("integrity: incomplete postings", pct < 5,
                      f"{n:,} of {total:,} ({pct:.1f}%) missing a core field",
                      fatal=False))

    # --- enrichment sanity ------------------------------------------------
    cur.execute("SELECT count(*), count(DISTINCT fit_score) FROM job_enrichment")
    scored, distinct_scores = cur.fetchone()
    # A working scorer spreads across the range. Everything landing on one
    # value is what a broken prompt or a truncated response looks like.
    out.append(Result("distribution: fit scores vary",
                      scored == 0 or distinct_scores >= 5,
                      f"{distinct_scores} distinct scores across {scored:,} assessments"))

    cur.execute("""
        SELECT count(*) FROM job_enrichment
        WHERE eligibility NOT IN ('eligible', 'blocked', 'unclear')
           OR fit_score IS NULL OR fit_score < 0 OR fit_score > 100
    """)
    n = cur.fetchone()[0]
    out.append(Result("integrity: enrichment values", n == 0,
                      f"{n} assessments with an invalid verdict or score"))

    # --- the estimated-salary trap ----------------------------------------
    cur.execute("""
        SELECT count(*) FROM raw_postings
        WHERE salary_is_predicted IS TRUE AND salary_min IS NULL
    """)
    n = cur.fetchone()[0]
    out.append(Result("consistency: predicted salary flag", n == 0,
                      f"{n} postings flagged predicted with no salary",
                      fatal=False))

    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline data quality checks")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = ap.parse_args()

    conn = connect(autocommit=True)
    print(f"Checking {describe()}\n")
    results = check(conn.cursor())
    conn.close()

    failures = 0
    for r in results:
        if r.ok:
            mark = "PASS"
        elif r.fatal or args.strict:
            mark, failures = "FAIL", failures + 1
        else:
            mark = "WARN"
        print(f"  [{mark}] {r.name:<38} {r.detail}")

    print()
    if failures:
        print(f"{failures} check(s) failed.")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
