"""
Compare model output between Postgres and Snowflake.

A build that succeeds on both engines is not a portability test. The bug this
project most fears compiled cleanly on both and returned different answers: an
unanchored regex matched 1,590 postings as internships on Postgres and zero on
Snowflake, because Postgres regexp_like searches anywhere while Snowflake's is
implicitly anchored. Nothing failed. The number was simply wrong on one side.

So the artefact worth having is not "it builds" but "they agree", and that means
running the same questions against both and comparing the answers.

Every check is a single scalar, deliberately. A scalar is unambiguous to compare,
cheap on a 2X-Small warehouse, and a difference points at one model rather than a
diff of 50,000 rows. The set is chosen to cover the predicates that have actually
been wrong before: the internship classifier, the sponsorship refusal, the
company grain, and the joins that carry them into the marts.

Usage:
    python storage/parity_check.py
    python storage/parity_check.py --json        # for a workflow summary
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect as pg_connect  # noqa: E402
from snowflake_db import connect as sf_connect  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("parity")

# Checked before the models are, and reported differently.
#
# The comparison only means something when the three engines are looking at the
# same rows. They are not automatically: Postgres is read live while the other two
# are mirrors, so anything that writes to the warehouse mid-run - a pipeline, or a
# person rebuilding the employer mapping by hand, which is what happened - leaves
# one engine a few thousand rows behind the others.
#
# That produces a "the engines disagree" failure which reads exactly like a SQL
# divergence and is nothing of the kind. Establishing first that the inputs match
# turns a day of looking for a dialect bug into one line saying the mirror is
# stale. The same confusion, in the other direction, is what made an earlier run
# compare a fresh Snowflake build against a nine-hour-old Postgres view.
SOURCES: dict[str, str] = {
    "source rows: raw_postings": "select count(*) from raw_postings",
    "source rows: posting_technologies": "select count(*) from posting_technologies",
    "source rows: company_employer_link": "select count(*) from company_employer_link",
    "source rows: dol_employer_summary": "select count(*) from dol_employer_summary",
}

# Each entry is one scalar that must match. Named for what a difference would
# mean, not for the SQL.
CHECKS: dict[str, str] = {
    # The exact bug class this exists for. A regex whose anchoring differs shows
    # up here and nowhere else.
    "postings classified as internships":
        "select count(*) from stg_jobs where is_internship",
    "postings with seniority intern":
        "select count(*) from stg_jobs where seniority = 'intern'",
    "postings whose text refuses sponsorship":
        "select count(*) from stg_jobs where refuses_sponsorship",
    "postings whose text offers sponsorship":
        "select count(*) from stg_jobs where offers_sponsorship",
    "postings seniority entry":
        "select count(*) from stg_jobs where seniority = 'entry'",
    "postings seniority senior":
        "select count(*) from stg_jobs where seniority = 'senior'",
    "stale postings":
        "select count(*) from stg_jobs where is_stale",

    # Grain. A company-name change on one engine would show here first.
    "rows in stg_jobs": "select count(*) from stg_jobs",
    "companies in dim_company": "select count(*) from dim_company",
    "distinct companies in stg_jobs":
        "select count(distinct company_name) from stg_jobs",

    # The sponsorship join, which is the feature most often wrong.
    "companies with confident sponsorship evidence":
        "select count(*) from int_company_sponsorship where is_confident_match",
    "total filings attributed to a company":
        "select sum(total_filings) from int_company_sponsorship where is_confident_match",

    # The marts the site reads.
    "rows in apply_queue": "select count(*) from apply_queue",
    "publishable rows in apply_queue":
        "select count(*) from apply_queue where link_tier = 'direct'",
    "intern/entry publishable rows":
        "select count(*) from apply_queue where link_tier = 'direct' "
        "and seniority in ('intern','entry')",
    "rows in ranked_opportunities": "select count(*) from ranked_opportunities",
    "technologies tracked": "select count(*) from dim_technology",
}


def scalar(cur, sql: str):
    cur.execute(sql)
    row = cur.fetchone()
    value = row[0] if row else None
    # Snowflake returns NUMBER as Decimal where Postgres returns int. Compare the
    # numbers, not their Python types, or every aggregate looks like a mismatch.
    return int(value) if value is not None else None


def every_check() -> dict[str, str]:
    """Sources first, then models. One definition, because two drifted."""
    return {**SOURCES, **CHECKS}


def databricks_scalars() -> dict[str, int | None] | None:
    """
    The same checks on Databricks, or None when it is not configured.

    Reached over the REST Statement Execution API rather than a driver, because
    Free Edition answers on no other channel - see storage/build_on_databricks.py.
    Optional on purpose: the third engine is the newest and the least essential,
    and a missing Databricks token must not stop Postgres and Snowflake being
    compared.
    """
    if not (os.getenv("DATABRICKS_HOST") and os.getenv("DATABRICKS_TOKEN")):
        return None
    try:
        from load_to_databricks import Databricks
    except ImportError:
        return None

    db = Databricks()
    out: dict[str, int | None] = {}
    failures = 0
    for name, sql in every_check().items():
        try:
            rows = db.sql(sql)
            # Every value comes back as text over REST, including counts.
            out[name] = int(rows[0][0]) if rows and rows[0][0] is not None else None
        except Exception as exc:                          # noqa: BLE001
            # Not just SystemExit. load_to_databricks raises that for a statement
            # the warehouse REJECTED, but a warehouse that will not answer at all
            # fails earlier, in raise_for_status, as requests.HTTPError - and that
            # one escaped this handler and took the whole parity run down with a
            # traceback instead of a verdict. The engine being unreachable is a
            # thing this function is supposed to report, not crash on.
            out[name] = None
            failures += 1
            log.error("  databricks failed on %r: %s: %s", name,
                      type(exc).__name__, " ".join(str(exc).split())[:100])

    # An engine that answered NOTHING is unreachable, not disagreeing.
    #
    # Free Edition deactivates a workspace that has gone unused, and every
    # statement then returns 400 with denyReason INACTIVE. Counting that as
    # twenty-one failed parity checks says the SQL diverged across engines, which
    # is false and is the most alarming thing this script can say. The
    # distinction is the whole point: some answers differing is a real finding,
    # no answers at all is an infrastructure fact.
    if failures == len(out):
        log.warning("Databricks answered none of the %s checks - treating it as "
                    "unavailable rather than as disagreeing. A Free Edition "
                    "workspace is deactivated after a period of no use; opening "
                    "it in a browser reactivates it.", len(out))
        return None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare model output across engines")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--skip-databricks", action="store_true",
                    help="compare Postgres and Snowflake only")
    args = ap.parse_args()

    pg = pg_connect(autocommit=True)
    sf = sf_connect()
    results = []
    try:
        with pg.cursor() as pc, sf.cursor() as sc:
            for name, sql in every_check().items():
                try:
                    a = scalar(pc, sql)
                except Exception as exc:                      # noqa: BLE001
                    a, pg_err = None, str(exc).strip()[:80]
                    # A dropped connection is the likeliest failure here - Neon
                    # closes an idle one, and this loop is long. Rolling back a
                    # connection that is already gone raises InterfaceError, so
                    # the handler crashed with a traceback and buried the error
                    # it was written to report.
                    try:
                        pg.rollback()
                    except Exception:                         # noqa: BLE001
                        pass
                    log.error("  postgres failed on %r: %s", name, pg_err)
                try:
                    b = scalar(sc, sql)
                except Exception as exc:                      # noqa: BLE001
                    b = None
                    log.error("  snowflake failed on %r: %s", name, str(exc).strip()[:80])
                results.append({"check": name, "postgres": a, "snowflake": b})
    finally:
        pg.close()
        sf.close()

    third = None if args.skip_databricks else databricks_scalars()
    for r in results:
        if third is not None:
            r["databricks"] = third.get(r["check"])
        # A missing third engine is not a disagreement. Everything present must
        # agree with Postgres.
        r["match"] = all(r[e] == r["postgres"] for e in ("snowflake", "databricks")
                         if e in r)

    engines = ["postgres", "snowflake"] + (["databricks"] if third is not None else [])

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        width = max(len(r["check"]) for r in results)
        header = " ".join(f"{e:>14}" for e in engines)
        log.info("%-*s %s", width, "check", header)
        for r in results:
            cells = [f"{r[e]:,}" if r.get(e) is not None else "-" for e in engines]
            log.info("%-*s %s  %s", width, r["check"],
                     " ".join(f"{c:>14}" for c in cells),
                     "ok" if r["match"] else "DIFFERS")

    if third is None and not args.skip_databricks:
        log.warning("Databricks was not compared: not configured, or unreachable.")

    # A stale mirror is not a disagreement about SQL, and saying so is the whole
    # point of checking the sources separately.
    stale = [r for r in results if not r["match"] and r["check"] in SOURCES]
    if stale:
        detail = ", ".join(r["check"].removeprefix("source rows: ") for r in stale)
        log.error("The engines are not looking at the same data: %s differ before a "
                  "single model is compared. Re-run the mirrors; this is not a SQL "
                  "difference.", detail)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::Mirrors are out of step ({detail}), so the engines were "
                  f"never comparable. Re-run after mirroring.")
        raise SystemExit(f"{len(stale)} source table(s) differ across engines")

    bad = [r for r in results if not r["match"]]
    if bad:
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::{len(bad)} parity check(s) differ across "
                  f"{len(engines)} engines: {', '.join(r['check'] for r in bad)}")
        raise SystemExit(f"{len(bad)} of {len(results)} checks differ")
    log.info("All %s checks agree across %s engines.", len(results), len(engines))


if __name__ == "__main__":
    main()
