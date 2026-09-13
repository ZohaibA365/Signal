"""
Build the models on Databricks, as the third parity engine.

Why this is not `dbt build --target databricks`, which is what it should be.
Databricks Free Edition does not expose its SQL warehouse over the driver
protocol at all: a databricks-sql-connector connect gets no response whatsoever,
with the warehouse awake and the same credentials working, and dbt-databricks uses
that connector - so it hangs forever with no output. Only the REST Statement
Execution API answers. That is the fifth Free Edition restriction this project has
hit by walking into it, after no python_file from a Volume, no SparkContext and no
RDDs. dbt-databricks was installed and tried before this was written; it is not a
dependency of anything here, because it cannot reach this workspace at all.

So the compiled SQL is taken from the dbt build that already happened and executed
over REST instead. That has one property worth stating plainly, because it cuts
both ways: this runs the EXACT SQL Postgres ran, not SQL re-rendered by a
Databricks adapter. It is therefore a harsher test than dbt would perform - an
adapter papers over dialect differences, which is its job, and papering over them
is precisely what a portability test must not do. It is also less representative
of how one would really run dbt here. For finding out whether the project's SQL
means the same thing on a third engine, the harsher reading is the useful one.

What it is looking for is a difference in answers rather than a difference in
syntax. A statement that fails to compile is a known dialect gap and is reported
as such; the interesting outcome is a model that builds on both and disagrees,
which is the shape of every cross-engine bug this project has actually had.

Usage:
    python storage/build_on_databricks.py
    python storage/build_on_databricks.py --only stg_jobs dim_company
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect as pg_connect  # noqa: E402
from load_to_databricks import CATALOG, SCHEMA, Databricks, mirror  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("build_on_databricks")

# dbt seeds, which live in Postgres as ordinary tables once `dbt seed` has run.
SEEDS = ["technologies"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "dbt_signal", "target", "manifest.json")


def models() -> list[dict]:
    """
    Every model, in an order where a model's inputs are built before it is.

    Read from dbt's manifest rather than inferred from the SQL: the manifest is
    what dbt itself used, so the order cannot disagree with the order the other
    engines were built in.
    """
    with open(MANIFEST) as fh:
        manifest = json.load(fh)
    nodes = {k: v for k, v in manifest["nodes"].items()
             if v["resource_type"] == "model"}

    ordered: list[dict] = []
    placed: set[str] = set()
    remaining = dict(nodes)
    while remaining:
        ready = [k for k, v in remaining.items()
                 if all(d in placed or d not in nodes for d in v["depends_on"]["nodes"])]
        if not ready:
            raise SystemExit(f"circular dependency among {sorted(remaining)}")
        for key in sorted(ready, key=lambda k: remaining[k]["name"]):
            ordered.append(remaining.pop(key))
            placed.add(key)
    return ordered


# The compiled SQL names the Postgres warehouse: "neondb"."public"."raw_postings".
# Every one of those becomes the mirrored table in Unity Catalog. Done textually,
# on a pattern anchored to the quoting dbt emits, so it cannot match anything
# inside a string literal or a comment by accident.
RELATION = re.compile(r'"[a-z_0-9]+"\."public"\."([a-z_0-9]+)"')

# Two rewrites that compensate for dbt's POSTGRES adapter, not for this project's
# SQL, and the distinction is the whole point of the exercise.
#
# Running Postgres-compiled SQL on Spark means dbt's own boilerplate arrives in
# Postgres dialect. generate_surrogate_key casts to TEXT, which Spark does not
# have - it is STRING - and star() quotes column names with double quotes, which
# Spark reads as string literals rather than identifiers. On a real Databricks
# adapter dbt would emit STRING and backticks itself. Shimming them here is doing
# by hand what the adapter would do, and it leaves every expression this project
# actually wrote untouched, which is what is being tested.
#
# Verified before relying on it: across all fifteen compiled models, every
# double-quoted token outside a comment is a plain lowercase identifier, so the
# rewrite cannot corrupt a string literal.
ADAPTER_SHIMS = (
    (re.compile(r"\bas TEXT\b"), "as STRING"),
    (re.compile(r'"([a-z_][a-z0-9_]*)"'), r"`\1`"),
)


def retarget(sql: str) -> str:
    """Point the SQL at the mirror, and speak the adapter's dialect for it."""
    sql = RELATION.sub(rf"{CATALOG}.{SCHEMA}.\1", sql)
    for pattern, replacement in ADAPTER_SHIMS:
        sql = pattern.sub(replacement, sql)
    return sql


def compiled_sql(node: dict) -> str:
    path = os.path.join(ROOT, "dbt_signal", "target", "compiled", node["compiled_path"]) \
        if node.get("compiled_path") else None
    if not path or not os.path.exists(path):
        path = os.path.join(ROOT, "dbt_signal", "target", "compiled", "signal",
                            node["original_file_path"])
    with open(path) as fh:
        return fh.read()


def build(db: Databricks, node: dict) -> tuple[bool, str]:
    """Create one model. Views stay views; everything else becomes a table."""
    name = node["name"]
    sql = retarget(compiled_sql(node))

    # Incremental models are built whole. Incrementality is a property of how the
    # table is maintained, not of what the SQL means, and the comparison is about
    # meaning.
    kind = "VIEW" if node["config"]["materialized"] == "view" else "TABLE"

    # An incremental model compiled normally selects from itself, to find the high
    # water mark. Here there is nothing to select from - the table is being created
    # in this statement - and the failure it produces names a missing relation,
    # which reads like a dependency-order bug rather than the compile-flag problem
    # it is. Caught here so the message says what to do.
    if f"{CATALOG}.{SCHEMA}.{name}" in sql:
        return False, ("compiled incrementally, so it selects from itself. Compile "
                       "with --full-refresh before building here.")

    statement = f"CREATE OR REPLACE {kind} {CATALOG}.{SCHEMA}.{name} AS\n{sql}"
    try:
        db.sql(statement)
        return True, ""
    except SystemExit as exc:
        # The engine's own complaint is the whole value of this exercise, so the
        # whole message is kept rather than its first line: Spark puts the SQLSTATE
        # and the offending line number after a newline, and taking line one threw
        # away every word of several errors.
        message = str(exc).removeprefix("statement failed: ")
        return False, " ".join(message.split())


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the models on Databricks over REST")
    ap.add_argument("--only", nargs="+", help="build just these models")
    args = ap.parse_args()

    nodes = models()
    if args.only:
        wanted = set(args.only)
        nodes = [n for n in nodes if n["name"] in wanted]

    db = Databricks()
    db.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

    # dbt's seeds are tables too, and `dbt build` creates them on the other two
    # engines as part of the run. Nothing runs dbt here, so the seed has to be
    # mirrored like a source or dim_technology fails on a missing relation.
    if not args.only:
        pg = pg_connect(autocommit=True)
        try:
            for seed in SEEDS:
                mirror(db, pg, seed)
        finally:
            pg.close()

    log.info("Building %s model(s) on %s.%s", len(nodes), CATALOG, SCHEMA)

    failures: list[tuple[str, str]] = []
    for node in nodes:
        ok, why = build(db, node)
        if ok:
            log.info("  %-28s %s  OK", node["name"], node["config"]["materialized"])
        else:
            failures.append((node["name"], why))
            log.error("  %-28s FAILED: %s", node["name"], why[:160])

    log.info("Done. %s built, %s failed.", len(nodes) - len(failures), len(failures))
    if failures:
        # An exit code, because a build that half worked must not read as success -
        # the Snowflake build once reported 44 passed and 65 silently skipped.
        log.error("dialect gaps to resolve:")
        for name, why in failures:
            log.error("  %s: %s", name, why[:300])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
