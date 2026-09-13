"""
What a mirror of the warehouse has to carry, in one place.

Two engines are now mirrored from Postgres for parity testing - Snowflake and
Databricks - and a table missing from either list does not fail loudly. The build
reports a Database Error on that source's tests and then SKIPS every model
downstream: the first full Snowflake build went 44 passed, 4 errored, 65 skipped,
so almost nothing was verified while the run looked like it had mostly worked.

A skipped model is the dangerous outcome, because a failing one announces itself.
Two hand-written copies of the same list is how that happens twice, so the list
lives here and both loaders import it.
"""

from __future__ import annotations

import json

# The source tables every dbt model reads from. Models are rebuilt by dbt on each
# engine; only these are copied.
TABLES = [
    "raw_postings",
    "posting_technologies",
    "job_enrichment",
    "market_snapshots",
    "market_snapshot_companies",
    "market_snapshot_salary",
    "dol_employer_summary",
    "company_employer_key",
    "company_employer_link",
    # stg_jobs joins this to canonicalise company names, so a build without it
    # fails three source tests and skips 65 downstream models. It was missing
    # because the list was hand-maintained in one script;
    # tests/test_mirror_tables.py now asserts the list covers every table dbt
    # declares as a source, and that both loaders read it from here.
    "company_identity",
]


def serialise_complex(df):
    """
    Turn Postgres arrays and JSONB into JSON strings.

    Neither has a pandas dtype the warehouse writers accept. Doing it identically
    for both engines is what keeps the comparison honest: if one engine received
    arrays and the other received strings, a model reading them would differ for a
    reason that has nothing to do with the SQL.

    No model currently selects one of these columns, which is why this can be a
    blanket conversion rather than a per-column decision.
    """
    for col in df.columns:
        sample = df[col].dropna().head(1)
        if len(sample) and isinstance(sample.iloc[0], (list, dict)):
            df[col] = df[col].map(lambda v: json.dumps(v) if v is not None else None)
    return df
