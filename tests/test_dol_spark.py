"""
Tests for the DOL Spark job's portability, not its arithmetic.

The arithmetic needs a JVM and this machine has none installed, which is itself
the finding: the job could not execute anywhere before Databricks, and it was in
no workflow either. So what is testable here - and what actually breaks when
someone moves a job between a laptop and a cluster - is the environment
detection and the path selection.

Importing the module is part of the test. pyspark imports cleanly without a JVM;
it only needs one when a session starts.
"""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "processing"))

import dol_spark  # noqa: E402


def test_detects_the_runtime_from_the_runtime_itself(monkeypatch):
    """
    DATABRICKS_RUNTIME_VERSION is set by the runtime, so it cannot drift out of
    step with reality the way a command-line flag can.
    """
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    assert dol_spark.on_databricks() is False
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    assert dol_spark.on_databricks() is True


def test_local_and_volume_paths_are_distinct():
    """
    A cluster has no data/ directory, and the laptop has no /Volumes/signal.
    Pointing either at the other's paths fails at read time, after the cluster
    has already started and begun costing quota.
    """
    assert dol_spark.IN_DIR != dol_spark.DBFS_IN
    assert dol_spark.OUT_DIR != dol_spark.DBFS_OUT
    assert dol_spark.DBFS_IN.startswith("/Volumes/")
    assert not dol_spark.IN_DIR.startswith("/")


def test_cluster_sizing_is_not_set_on_databricks():
    """
    Driver memory and shuffle partitions belong to the cluster, not to this file.
    Setting them inside a Databricks runtime is either rejected or silently
    ignored, and either way it misstates who is in charge of sizing.
    """
    import inspect
    src = inspect.getsource(dol_spark.session)
    assert "if not on_databricks()" in src
    assert "spark.driver.memory" in src


@pytest.mark.parametrize("name", ["build", "session", "on_databricks", "main"])
def test_the_job_still_exposes_its_entry_points(name):
    """
    build() must stay callable with an explicit session, which is what lets the
    same function run in both places and what a notebook would import.
    """
    assert callable(getattr(dol_spark, name))


def test_build_takes_a_session_rather_than_creating_one():
    """
    The separation that makes this portable at all: if build() created its own
    session it would carry laptop config onto the cluster.
    """
    import inspect
    params = list(inspect.signature(dol_spark.build).parameters)
    assert params[0] == "spark"
    assert "in_dir" in params and "out_dir" in params


def test_module_imports_without_a_jvm():
    """The condition this machine is actually in."""
    importlib.reload(dol_spark)
