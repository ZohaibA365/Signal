# Orchestration

The daily pipeline as an Airflow DAG.

## Why a separate virtualenv

Airflow pins its entire dependency tree through an official constraints file.
The pipeline needs pandas 3.x and dbt; Airflow 2.10 does not agree with those
pins. Forcing both into one environment is the usual way these installs break,
so Airflow gets `.venv-airflow/` and the DAG shells out to the project's
`.venv/`. The DAG therefore stays independent of what the pipeline depends on.

## Setup

```bash
python3 -m venv .venv-airflow
.venv-airflow/bin/pip install "apache-airflow==2.10.5" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt"

export AIRFLOW_HOME="$PWD/.airflow"
export AIRFLOW__CORE__DAGS_FOLDER="$PWD/orchestration"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export SIGNAL_HOME="$PWD"

.venv-airflow/bin/airflow db migrate
```

## Running

```bash
# one task, no scheduler needed - the fastest way to check a change
.venv-airflow/bin/airflow tasks test signal_daily quality_checks 2026-08-28

# the whole graph
.venv-airflow/bin/airflow dags test signal_daily 2026-08-28

# scheduler + UI
.venv-airflow/bin/airflow standalone
```

## The graph

```
start ─┬─ market_snapshot ──────────────────────────────┐
       └─ ingest_postings → load_warehouse →            │
          extract_technologies → dbt_build →            │
          enrich_new_roles → dbt_refresh_marts ─────────┴─→ quality_checks
                                                              → export_parquet → done
```

Design decisions that are not obvious from the graph:

- **Snapshot and ingest run in parallel.** They share no data and the snapshot
  is time-sensitive.
- **`market_snapshot` retries four times, more than anything else.** It is the
  only step that cannot be backfilled - the source API reports today's demand
  and nothing else, so a missed run is a permanent hole in the series.
- **`quality_checks` has zero retries.** A failing quality check is a real
  problem; retrying only delays the alert.
- **Quality gates publication, and waits on the snapshot too**, so a failed
  capture is caught before anything is exported.
- **`dbt_refresh_marts` runs after enrichment.** Without it `apply_queue`
  keeps yesterday's verdicts while `job_enrichment` already holds today's.
- **Enrichment is capped at 150 roles.** It is the only task that spends money,
  and an unbounded run against a large ingest could empty the account - which
  has already happened once.
- **`catchup=False`.** Backfilling this DAG would re-run snapshots for past
  dates, and the API cannot answer for the past, so the runs would silently
  record today's numbers under old dates.
