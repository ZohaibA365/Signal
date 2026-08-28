"""
Aggregate DOL H-1B filings to employer level with Spark.

This is the job that turns sponsorship from a guess into a record. Signal has
been inferring eligibility from job text, and the text almost never says: only
~2% of postings mention citizenship or sponsorship at all. These filings state
who actually sponsored, for which role, at what wage.

Why Spark rather than pandas: six quarters of filings across millions of rows,
read as a partitioned Parquet dataset, aggregated on a wide grouping key with
several windowed measures. Pandas can be pushed through it; Spark reads only
the columns and partitions it needs, spills rather than dies, and the same job
runs unchanged against more years. That is the honest justification - not the
raw size of any one file.

Output: one row per (employer, fiscal year) with filing counts, approval mix,
wage percentiles and the roles they sponsor for, written back as Parquet and
loaded into the warehouse by storage/load_dol.py.

Usage:
    JAVA_HOME=$(brew --prefix openjdk@17) python processing/dol_spark.py
"""

from __future__ import annotations

import argparse
import logging

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("dol_spark")

IN_DIR = "data/dol_parquet"
OUT_DIR = "data/dol_employer_summary"

# Titles that indicate data/software work, so the summary can answer "does this
# employer sponsor for roles like mine" rather than only "does it sponsor".
TECH_TITLE = (
    r"(?i)(data engineer|data scientist|software|machine learning|analytics|"
    r"database|devops|site reliability|cloud|platform engineer|analyst)"
)


def build(spark: SparkSession, in_dir: str, out_dir: str) -> None:
    df = spark.read.parquet(in_dir)
    log.info("Loaded %s filings across %s partitions",
             f"{df.count():,}", df.rdd.getNumPartitions())

    df = (
        df.filter(F.col("employer_key").isNotNull())
          # CERTIFIED and CERTIFIED-WITHDRAWN both mean the DOL approved the
          # application; withdrawn afterwards is an employer decision and still
          # evidences willingness to sponsor.
          .withColumn("is_certified",
                      F.col("case_status").rlike("(?i)^certified"))
          .withColumn("is_tech_role", F.col("job_title").rlike(TECH_TITLE))
          # Wages outside this band are data-entry errors, and a single $9m
          # outlier would drag an employer's median into nonsense.
          .withColumn("clean_wage",
                      F.when((F.col("annual_wage") >= 20000) &
                             (F.col("annual_wage") <= 1000000),
                             F.col("annual_wage")))
    )

    summary = (
        df.groupBy("employer_key", "fiscal_year")
          .agg(
              F.first("employer_name", ignorenulls=True).alias("employer_name"),
              F.count("*").alias("filings"),
              F.sum(F.col("is_certified").cast("int")).alias("certified"),
              F.sum(F.col("is_tech_role").cast("int")).alias("tech_filings"),
              F.countDistinct("job_title").alias("distinct_titles"),
              F.countDistinct("worksite_state").alias("distinct_states"),
              F.expr("percentile_approx(clean_wage, 0.5)").alias("median_wage"),
              F.expr("percentile_approx(clean_wage, 0.25)").alias("p25_wage"),
              F.expr("percentile_approx(clean_wage, 0.75)").alias("p75_wage"),
              F.max("clean_wage").alias("max_wage"),
              F.collect_set(F.when(F.col("is_tech_role"), F.col("soc_title"))
                             ).alias("tech_soc_titles"),
          )
          .withColumn("certified_pct",
                      F.round(100.0 * F.col("certified") / F.col("filings"), 1))
          .withColumn("tech_pct",
                      F.round(100.0 * F.col("tech_filings") / F.col("filings"), 1))
    )

    # Rank employers within each year so "a major sponsor" is a measured
    # statement rather than an impression.
    w = Window.partitionBy("fiscal_year").orderBy(F.desc("filings"))
    summary = (
        summary.withColumn("rank_in_year", F.rank().over(w))
               .withColumn("tech_soc_titles",
                           F.slice(F.array_sort(F.col("tech_soc_titles")), 1, 5))
    )

    summary.write.mode("overwrite").partitionBy("fiscal_year").parquet(out_dir)
    log.info("Wrote employer summary -> %s", out_dir)

    log.info("Top sponsors by filings (most recent year):")
    latest = summary.agg(F.max("fiscal_year")).collect()[0][0]
    for r in (summary.filter(F.col("fiscal_year") == latest)
                     .orderBy(F.desc("filings")).limit(8).collect()):
        log.info("  %-42s %6s filings  %5.1f%% certified  median $%s",
                 r["employer_name"][:42], f"{r['filings']:,}",
                 r["certified_pct"] or 0,
                 f"{int(r['median_wage']):,}" if r["median_wage"] else "n/a")


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate DOL filings with Spark")
    ap.add_argument("--in-dir", default=IN_DIR)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    spark = (
        SparkSession.builder
        .appName("signal-dol-employer-summary")
        .config("spark.sql.shuffle.partitions", "16")   # laptop-sized, not cluster-sized
        .config("spark.driver.memory", "4g")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    try:
        build(spark, args.in_dir, args.out_dir)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
