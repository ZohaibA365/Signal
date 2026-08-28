"""
Signal daily pipeline.

Runs the whole chain every morning: capture the market, pull new postings,
load, extract technologies, transform, score, validate, publish.

Why this is a DAG rather than a shell script with `&&`:

  * The snapshot and the posting ingest are independent and run in parallel.
  * Steps have genuinely different failure modes. A rate-limited ingest should
    retry with backoff; a failed data-quality check should stop the run and
    alert, because publishing bad numbers is worse than publishing none.
  * The market snapshot is the one step that cannot be backfilled - the API
    only reports today - so it is marked to alert on failure specifically.
  * Enrichment costs money, so it is capped and depends on everything before
    it succeeding.

Tasks shell out to the project venv rather than importing the code, so the
DAG stays independent of the pipeline's dependency tree - Airflow pins its
own, and forcing them into one environment is how these installs break.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

PROJECT = os.environ.get("SIGNAL_HOME", "/Users/zohaib/Documents/GitHub/Signal")
PY = f"{PROJECT}/.venv/bin/python"
DBT = f"{PROJECT}/.venv/bin/dbt"

# Every task loads .env the same way, so credentials live in one place.
ENV = f"set -a && . {PROJECT}/.env && set +a"

DEFAULT_ARGS = {
    "owner": "signal",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=45),
}


def bash(task_id: str, command: str, dag: DAG, **kwargs) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=f"cd {PROJECT} && {ENV} && {command}",
        dag=dag,
        **kwargs,
    )


with DAG(
    dag_id="signal_daily",
    description="Daily job-market capture, transformation, scoring and validation",
    schedule="0 7 * * *",                 # 07:00 UTC
    start_date=datetime(2026, 8, 1),
    catchup=False,                        # a missed snapshot cannot be recreated
    max_active_runs=1,                    # runs share warehouse tables
    default_args=DEFAULT_ARGS,
    tags=["signal", "daily"],
) as dag:

    start = EmptyOperator(task_id="start")

    # --- capture -----------------------------------------------------------
    # The compounding asset. A gap here is permanent, so it gets more retries
    # than anything else in the DAG.
    snapshot = bash(
        "market_snapshot",
        f"{PY} ingestion/market_snapshot.py",
        dag,
        retries=4,
        retry_delay=timedelta(minutes=10),
    )

    ingest = bash(
        "ingest_postings",
        f"{PY} ingestion/adzuna_ingest.py --pages 5 --country us ca --resume",
        dag,
    )

    # --- load and transform ------------------------------------------------
    load = bash(
        "load_warehouse",
        f"{PY} storage/load_to_warehouse.py --country us ca",
        dag,
    )

    extract = bash(
        "extract_technologies",
        f"{PY} ai_layer/extract_tech.py",
        dag,
    )

    transform = bash(
        "dbt_build",
        f"DO_NOT_TRACK=1 SSL_CERT_FILE=$({PY} -c 'import certifi;print(certifi.where())') "
        f"{DBT} build --project-dir dbt_signal --profiles-dir dbt_signal --no-version-check",
        dag,
    )

    # --- scoring -----------------------------------------------------------
    # Capped deliberately: this is the only task that spends money, and an
    # unbounded run against a large ingest could empty the account. The
    # enricher itself skips postings whose text has not changed.
    enrich = bash(
        "enrich_new_roles",
        f"PYTHONPATH=ai_layer {PY} ai_layer/enrich.py --seniority intern entry --limit 150",
        dag,
        execution_timeout=timedelta(hours=2),
        retries=1,
    )

    # Marts must be rebuilt after scoring, or apply_queue keeps yesterday's
    # verdicts while job_enrichment has today's.
    refresh_marts = bash(
        "dbt_refresh_marts",
        f"DO_NOT_TRACK=1 SSL_CERT_FILE=$({PY} -c 'import certifi;print(certifi.where())') "
        f"{DBT} build --project-dir dbt_signal --profiles-dir dbt_signal --no-version-check "
        f"--select marts.*+",
        dag,
    )

    # --- validate and publish ----------------------------------------------
    # No retries: a failing quality check is a real problem, and retrying it
    # only delays the alert.
    quality = bash(
        "quality_checks",
        f"{PY} quality/expectations.py",
        dag,
        retries=0,
    )

    publish = bash(
        "export_parquet",
        f"{PY} storage/export_parquet.py",
        dag,
    )

    done = EmptyOperator(task_id="done")

    # Snapshot and ingest are independent, so they run in parallel.
    start >> [snapshot, ingest]
    ingest >> load >> extract >> transform >> enrich >> refresh_marts
    # Quality gates publication, and waits on the snapshot too so a failed
    # capture is caught before anything is exported.
    [refresh_marts, snapshot] >> quality >> publish >> done
