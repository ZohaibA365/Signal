"""
Tests for the DOL Spark job's portability, not its arithmetic.

The arithmetic needs a JVM and this machine has none installed, which is itself
the finding: the job could not execute anywhere before Databricks, and it was in
no workflow either. What is testable - and what actually breaks when a job moves
between a laptop and a cluster - is the environment detection and the paths.

This file needs no pyspark, deliberately, and asserts by reading the source.
pyspark is declared in requirements-spark.txt and not in requirements-dev.txt so
an optional quarterly component cannot break the install every daily step depends
on - which means CI has no pyspark, and importing the job module there raises
ModuleNotFoundError. The first version of this file did exactly that,
contradicting a decision made in the same commit.

A module-level importorskip was the obvious fix and the wrong one: it skips the
whole file, so CI would have checked nothing. The import-dependent tests live in
test_dol_spark_runtime.py instead, and everything that can be checked as text is
checked here, where it runs everywhere. That keeps coverage on the part most
likely to rot - the conditional sizing and the two sets of paths.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "processing" / "dol_spark.py"
SOURCE = JOB.read_text()


# --------------------------------------------------- no pyspark required

def test_cluster_sizing_is_not_set_on_databricks():
    """
    Driver memory and shuffle partitions belong to whoever owns the compute.
    Setting them inside a Databricks runtime is either rejected or silently
    ignored, and either way it misstates who is in charge of sizing.
    """
    assert "if not on_databricks():" in SOURCE
    sizing = SOURCE.index("spark.driver.memory")
    guard = SOURCE.index("if not on_databricks():")
    assert guard < sizing, "driver memory is set outside the local-only branch"


def test_the_runtime_is_detected_from_the_runtime():
    """
    DATABRICKS_RUNTIME_VERSION is set by the runtime itself, so it cannot drift
    out of step with reality the way a command-line flag can.
    """
    assert "DATABRICKS_RUNTIME_VERSION" in SOURCE


def test_local_and_volume_paths_are_distinct():
    """
    A cluster has no data/ directory and a laptop has no /Volumes/signal.
    Pointing either at the other's paths fails at read time, after the cluster
    has started and begun costing quota.
    """
    paths = dict(re.findall(r'^(IN_DIR|OUT_DIR|DBFS_IN|DBFS_OUT)\s*=\s*"([^"]+)"',
                            SOURCE, re.M))
    assert set(paths) == {"IN_DIR", "OUT_DIR", "DBFS_IN", "DBFS_OUT"}
    assert paths["IN_DIR"] != paths["DBFS_IN"]
    assert paths["OUT_DIR"] != paths["DBFS_OUT"]
    assert paths["DBFS_IN"].startswith("/Volumes/")
    assert not paths["IN_DIR"].startswith("/")


def test_build_takes_a_session_rather_than_creating_one():
    """
    The separation that makes this portable: a build() that created its own
    session would carry laptop config onto the cluster.
    """
    sig = re.search(r"def build\(([^)]*)\)", SOURCE).group(1)
    assert sig.split(",")[0].split(":")[0].strip() == "spark"
    assert "in_dir" in sig and "out_dir" in sig


def test_pyspark_is_declared_in_its_own_requirements_file():
    """
    The reason the tests below are skippable. An optional component's install
    must not be able to break the one every daily step depends on.
    """
    spark_reqs = (ROOT / "requirements-spark.txt").read_text()
    dev_reqs = (ROOT / "requirements-dev.txt").read_text()
    assert "pyspark" in spark_reqs
    assert "pyspark" not in dev_reqs
