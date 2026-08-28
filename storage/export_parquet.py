"""
Curated Parquet layer in the data lake.

The raw layer stores exactly what each API returned, as JSON, because a raw
layer's job is fidelity. That format is wrong for everything downstream:
JSON is row-oriented, uncompressed, and has to be fully parsed to read one
column.

This writes a curated layer in Parquet - columnar, compressed, and
partitioned - which matters for two concrete reasons rather than as a
box-tick:

  1. Spark cannot read the DOL H-1B XLSX files at all, so a Parquet
     conversion is a hard prerequisite for that work, not a nicety.
  2. Analytical scans read a few columns out of many. Parquet reads only the
     columns asked for, and partition pruning skips whole files.

Partitioned by source and posting month, which is how the data is actually
queried: "this source, this period".

Usage:
    python storage/export_parquet.py                 # postings + market
    python storage/export_parquet.py --table postings --local /tmp/out
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import warnings

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import connect, describe  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("export_parquet")

EXPORTS = {
    "postings": {
        "sql": """
            SELECT source, job_id, country, company_name, job_title,
                   location_state, posted_date, seniority, days_since_posted,
                   salary_min_reported, is_internship, is_stale
            FROM stg_jobs
            WHERE posted_date IS NOT NULL
        """,
        "partition_on": ["source", "posted_month"],
        "prefix": "curated/postings",
    },
    "market": {
        "sql": """
            SELECT snapshot_date, tech_slug, openings, rank_overall,
                   rank_in_category, pct_of_category
            FROM fct_market_snapshot
        """,
        "partition_on": ["snapshot_month"],
        "prefix": "curated/market_snapshot",
    },
}


def add_partition_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "posted_date" in df.columns:
        df["posted_month"] = pd.to_datetime(df["posted_date"], utc=True).dt.strftime("%Y-%m")
    if "snapshot_date" in df.columns:
        df["snapshot_month"] = pd.to_datetime(df["snapshot_date"]).dt.strftime("%Y-%m")
    return df


def export(name: str, spec: dict, conn, destination: str | None) -> None:
    log.info("Exporting %s ...", name)
    with warnings.catch_warnings():
        # pandas prefers SQLAlchemy; psycopg2 works fine for a read-only
        # SELECT and adding SQLAlchemy for one query is not worth it.
        warnings.simplefilter("ignore", UserWarning)
        df = pd.read_sql(spec["sql"], conn)
    if df.empty:
        log.warning("  %s produced no rows, skipping", name)
        return
    df = add_partition_columns(df)

    json_bytes = len(df.to_json(orient="records").encode())
    table = pa.Table.from_pandas(df, preserve_index=False)

    if destination:
        path = os.path.join(destination, spec["prefix"])
        pq.write_to_dataset(table, root_path=path, partition_cols=spec["partition_on"],
                            compression="snappy", existing_data_behavior="delete_matching")
        size = sum(os.path.getsize(os.path.join(dp, f))
                   for dp, _, fs in os.walk(path) for f in fs)
        log.info("  %s rows -> %s", f"{len(df):,}", path)
    else:
        # Write one file per partition directly to S3.
        bucket = os.getenv("S3_BUCKET")
        s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
        size = 0
        for keys, group in df.groupby(spec["partition_on"], dropna=False):
            keys = keys if isinstance(keys, tuple) else (keys,)
            parts = "/".join(f"{col}={val}" for col, val in zip(spec["partition_on"], keys, strict=True))
            buf = io.BytesIO()
            pq.write_table(pa.Table.from_pandas(group.drop(columns=spec["partition_on"]),
                                                preserve_index=False),
                           buf, compression="snappy")
            body = buf.getvalue()
            size += len(body)
            s3.put_object(Bucket=bucket, Key=f"{spec['prefix']}/{parts}/data.parquet",
                          Body=body)
        log.info("  %s rows -> s3://%s/%s/", f"{len(df):,}", bucket, spec["prefix"])

    log.info("  %.1f MB parquet vs %.1f MB json  (%.1fx smaller)",
             size / 1e6, json_bytes / 1e6, json_bytes / size if size else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Export curated Parquet to the data lake")
    ap.add_argument("--table", choices=list(EXPORTS) + ["all"], default="all")
    ap.add_argument("--local", help="write to a local directory instead of S3")
    args = ap.parse_args()

    conn = connect(autocommit=True)
    log.info("Reading from %s", describe())
    for name, spec in EXPORTS.items():
        if args.table in (name, "all"):
            export(name, spec, conn, args.local)
    conn.close()


if __name__ == "__main__":
    main()
