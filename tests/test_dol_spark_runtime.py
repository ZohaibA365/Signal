"""
Tests for the Spark job that need pyspark importable.

Separate file because pyspark is in requirements-spark.txt and not in
requirements-dev.txt, so CI does not have it and these skip there. The assertions
that can be made without importing the job are in test_dol_spark.py, which runs
everywhere - a module-level skip in one combined file would have meant CI checked
nothing at all.
"""

import inspect
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "pyspark",
    reason="pyspark is declared in requirements-spark.txt, not requirements-dev.txt",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "processing"))

import dol_spark  # noqa: E402


def test_detects_the_runtime(monkeypatch):
    """
    DATABRICKS_RUNTIME_VERSION is set by the runtime itself, so it cannot drift
    out of step with reality the way a command-line flag can.
    """
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    assert dol_spark.on_databricks() is False
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    assert dol_spark.on_databricks() is True


@pytest.mark.parametrize("name", ["build", "session", "on_databricks", "main"])
def test_entry_points_exist(name):
    assert callable(getattr(dol_spark, name))


def test_session_creation_is_isolated_from_the_job_logic():
    """
    build() must not create a session, or it carries laptop config onto a cluster.
    """
    assert "SparkSession" in inspect.getsource(dol_spark.session)
    assert "SparkSession.builder" not in inspect.getsource(dol_spark.build)


def test_the_module_imports_without_a_jvm():
    """
    The condition this machine is in: pyspark installed, no JVM. Import must
    work; only starting a session needs the JVM.
    """
    assert dol_spark.__name__ == "dol_spark"
