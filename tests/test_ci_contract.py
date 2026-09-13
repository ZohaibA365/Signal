"""
The rules CI depends on, checked without CI.

Two of this project's costliest failures were not bugs in the pipeline. They were
breaches of a contract between the code and the runner, each invisible on a
laptop where the missing thing happens to be installed or already built:

  - a test imported a package that only the optional Snowflake requirements
    provide, so pytest could not even collect the suite and exited 2. The same
    mistake had already happened once with pyspark.
  - the parity workflow compared a freshly built Snowflake against a Postgres
    view built nine hours earlier from a different commit, so two engines running
    different SQL were reported as an engine disagreement.

Both are properties of text, so both can be checked in a second with no database,
no network and no runner.
"""

import ast
import re
import sys
from functools import cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTS = sorted((ROOT / "tests").glob("test_*.py"))


@cache
def declared_packages() -> set[str]:
    """
    Everything CI installs, as import names.

    Only requirements-dev.txt and what it includes. The Snowflake and Spark files
    are deliberately excluded: that they are NOT installed in CI is the whole
    point of keeping them separate.
    """
    names: set[str] = set()
    for path in (ROOT / "requirements.txt", ROOT / "requirements-dev.txt"):
        for line in path.read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            name = re.split(r"[=<>!\[;]", line)[0].strip().lower()
            names.add(name.replace("-", "_"))
    # Distribution names differ from import names for a handful of packages.
    names |= {"dotenv", "psycopg2", "yaml", "dateutil", "bs4", "PIL"}
    return names


@cache
def first_party() -> set[str]:
    """
    Every module name this repository itself provides.

    Derived from the filesystem rather than listed, because a hand-kept list of
    our own module names is a list that goes stale and then fails a test for the
    wrong reason. The tests insert a package directory onto sys.path and import
    the module bare, so both the directory names and the file stems count.

    Cached: this walks the repository, and it is asked once per test file.
    """
    skip = {".venv", ".git", "__pycache__", "target", "dbt_packages", "logs", "data"}
    names = {p.name for p in ROOT.rglob("*")
             if p.is_dir() and not skip & set(p.parts) and not p.name.startswith(".")}
    names |= {p.stem for p in ROOT.rglob("*.py") if not skip & set(p.parts)}
    return names


def module_level_imports(path: Path) -> set[str]:
    """Top-level package names imported at module scope, not inside a function."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in tree.body:                      # module scope only
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", TESTS, ids=lambda p: p.name)
def test_tests_import_only_what_ci_installs(path):
    """
    A test may not import, at module scope, a package CI does not install.

    pytest exits 2 on a collection error, so one such import does not fail one
    test - it fails every test, and the run reports nothing about the code. Needs
    of an optional package belong behind pytest.importorskip inside the file that
    needs them, which is what tests/test_snowflake_key_parses.py does.
    """
    allowed = declared_packages() | first_party() | set(sys.stdlib_module_names)
    offenders = sorted(module_level_imports(path) - allowed)
    assert not offenders, (
        f"{path.name} imports {offenders} at module scope, which CI does not "
        f"install. Move it inside the test, or use pytest.importorskip at the "
        f"top of a file that holds only tests needing it."
    )


@pytest.fixture(scope="module")
def steps() -> list[str]:
    spec = yaml.safe_load((ROOT / ".github/workflows/parity.yml").read_text())
    return [s.get("name") or s.get("uses") or "" for s in spec["jobs"]["parity"]["steps"]]


def order(steps: list[str], fragment: str) -> int:
    for i, name in enumerate(steps):
        if fragment.lower() in name.lower():
            return i
    raise AssertionError(f"parity.yml has no step matching {fragment!r}")


class TestParityComparesLikeWithLike:
    """
    The parity workflow's step order is its correctness.

    Comparing two engines only means something when both ran the same SQL over
    the same data. Each of these assertions corresponds to a way the run reported
    a disagreement that was not one.
    """


    def test_postgres_is_rebuilt_before_snowflake_is_mirrored(self, steps):
        """Otherwise Postgres holds whatever the last daily run built."""
        assert order(steps, "Rebuild the models on Postgres") < \
               order(steps, "Mirror the source tables")

    def test_verdicts_are_evaluated_before_either_engine_builds(self, steps):
        """A half-evaluated column differs for reasons of loading order."""
        assert order(steps, "sponsorship verdicts") < \
               order(steps, "Rebuild the models on Postgres")

    def test_databricks_is_built_before_the_comparison(self, steps):
        """The third engine is only compared if it was rebuilt from this commit."""
        assert order(steps, "Mirror the source tables to Databricks") < \
               order(steps, "Build every model on Databricks") < \
               order(steps, "Compare the engines")

    def test_the_third_engine_is_allowed_to_fail(self, steps):
        """
        Postgres and Snowflake are the established pair. A Databricks outage must
        not take the comparison with it - the check reports a missing engine rather
        than claiming three agreed.
        """
        spec = yaml.safe_load((ROOT / ".github/workflows/parity.yml").read_text())
        by_name = {s.get("name"): s for s in spec["jobs"]["parity"]["steps"]}
        for name in ("Mirror the source tables to Databricks",
                     "Build every model on Databricks"):
            assert by_name[name].get("continue-on-error") is True, name

    def test_databricks_is_compiled_with_full_refresh(self, steps):
        """
        An incremental model compiled the usual way selects from itself, and on
        Databricks the table is being created by that same statement.
        """
        spec = yaml.safe_load((ROOT / ".github/workflows/parity.yml").read_text())
        by_name = {s.get("name"): s for s in spec["jobs"]["parity"]["steps"]}
        assert "--full-refresh" in by_name["Build every model on Databricks"]["run"]

    def test_the_comparison_is_last(self, steps):
        last = max(order(steps, s) for s in (
            "Compare the engines", "Build every model on Snowflake",
            "Build every model on Databricks", "Rebuild the models on Postgres"))
        assert order(steps, "Compare the engines") == last

    def test_it_shares_the_pipeline_lock(self, steps):
        """Two dbt builds dropping the same tables at once fail uselessly."""
        spec = yaml.safe_load((ROOT / ".github/workflows/parity.yml").read_text())
        assert spec["concurrency"]["group"] == "daily-pipeline"

    def test_both_workflows_derive_credentials_the_same_way(self):
        """
        dbt reads NEON_USER and NEON_PASSWORD, which exist in neither the repo nor
        a runner. The daily pipeline learned that once; parity inherited the same
        script rather than its own copy to drift from.
        """
        for name in ("daily.yml", "parity.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            assert "scripts/derive_dbt_credentials.sh" in text, name
