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
    # Installed as a dependency of something declared above, and imported directly
    # by name. botocore ships with boto3 and cannot be absent while boto3 is there.
    names |= {"botocore", "jinja2", "markupsafe", "certifi", "urllib3"}
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
    """
    Every package a test file imports, wherever the import is written.

    This walked module scope only, on the theory that a deferred import is the FIX
    for an optional dependency. It is half the fix. Moving the import inside a
    function stops pytest exiting 2 on a collection error, so the other tests still
    run - but the test doing the importing still fails, and it fails only in CI,
    because a laptop has the optional package installed. That is precisely the
    shape of bug this file exists to prevent, and it got through: a test reached
    clean_sender through service/app.py, which imports FastAPI, which CI does not
    install.

    So every import counts now, and a file that genuinely needs an optional package
    says so with pytest.importorskip - which is what the message below asks for and
    what tests/test_snowflake_key_parses.py already does.
    """
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found - skipped_packages(tree)


def skipped_packages(tree: ast.Module) -> set[str]:
    """Packages the file already guards with pytest.importorskip("name")."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "importorskip"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.add(node.args[0].value.split(".")[0])
    return out


SKIP_DIRS = {".venv", ".venv-airflow", ".git", "__pycache__", "target",
             "dbt_packages", "logs", "data", "node_modules"}


def search_path(path: Path) -> list[Path]:
    """
    The directories this test file puts on sys.path, in the order it puts them.

    Read out of the file rather than guessed, because resolution order decides
    WHICH module a bare name means. Two files here are called app.py - the public
    service and the Streamlit dashboard - and they import entirely different third
    party packages, so guessing would either miss a real breakage or invent one.
    """
    dirs = [ROOT]
    for node in ast.walk(ast.parse(path.read_text())):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        # A docstring is a string constant too, and joining one onto ROOT asks the
        # filesystem about a path several hundred characters long. Only things
        # shaped like a single directory name are considered.
        value = node.value
        if not value or len(value) > 40 or not re.fullmatch(r"[\w.-]+", value):
            continue
        candidate = ROOT / value
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    return dirs


@cache
def first_party_modules() -> dict[str, tuple[Path, ...]]:
    """Every module this repository provides, by name, in no particular order."""
    out: dict[str, list[Path]] = {}
    for file in ROOT.rglob("*.py"):
        if SKIP_DIRS & set(file.parts):
            continue
        out.setdefault(file.stem, []).append(file)
        # A package directory is a module name too - `import ats` finds
        # ingestion/ats/. Its __init__ is what importing it runs.
        if file.name == "__init__.py":
            out.setdefault(file.parent.name, []).append(file)
    return {k: tuple(v) for k, v in out.items()}


def resolve_module(name: str, dirs: list[Path]) -> Path | None:
    known = first_party_modules().get(name, ())
    for directory in dirs:
        for candidate in known:
            if candidate.parent == directory:
                return candidate
    return known[0] if known else None


def required_packages(path: Path, dirs: list[Path], seen: set[Path] | None = None,
                      top: bool = True, guarded: set[str] | None = None) -> set[str]:
    """
    Every third-party package importing this file actually requires.

    Transitively, and that is the point. The direct-imports check passed a test
    that imported service/app.py - a first-party name, so nothing to object to -
    while app.py imports FastAPI at module scope, which CI does not install. The
    test therefore failed in CI and nowhere else, which is the exact failure this
    file was written to prevent and did not.

    Within the file being checked, an import anywhere counts, including inside a
    function: a deferred import stops the collection error but still fails the
    test. Within an imported MODULE, only module scope counts, because that is what
    importing it runs.
    """
    seen = seen if seen is not None else set()
    if path in seen:
        return set()
    seen.add(path)

    tree = ast.parse(path.read_text())
    nodes = list(ast.walk(tree)) if top else list(tree.body)
    # A guard applies to everything the guarded import reaches, not only to the
    # line it sits above: tests/test_dol_spark_runtime.py calls importorskip on
    # pyspark and then imports the job module, and the job module is what imports
    # pyspark. Carried down the recursion rather than applied at each level.
    guarded = (guarded or set()) | skipped_packages(tree)

    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    names -= guarded

    out: set[str] = set()
    for name in names:
        target = resolve_module(name, dirs) if name in first_party_modules() else None
        if target is not None:
            out |= required_packages(target, dirs, seen, top=False, guarded=guarded)
        else:
            out.add(name)
    return out


@pytest.mark.parametrize("path", TESTS, ids=lambda p: p.name)
def test_a_test_never_needs_a_package_ci_does_not_install(path):
    """
    Transitively. Importing a first-party module imports everything it imports.

    Both times this rule has been broken, the missing package was two steps away:
    a test imported a module that imported cryptography, and later one that
    imported FastAPI. Each passed locally, where the optional package happens to be
    installed, and failed every test in CI.
    """
    allowed = declared_packages() | set(sys.stdlib_module_names)
    offenders = sorted(n for n in required_packages(path, search_path(path))
                       if n not in allowed)
    assert not offenders, (
        f"{path.name} needs {offenders}, which CI does not install - directly or "
        f"through a first-party module it imports. Import the logic from a module "
        f"without that dependency, or guard it with pytest.importorskip."
    )


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


def ignored(path: Path) -> bool:
    """
    Is git actually going to skip this file?

    `git check-ignore` exits 0 both for an ignored path and for one rescued by a
    negation, so the exit code alone answers a different question than it appears
    to - a first version of this test passed for that reason. The verbose output
    names the winning rule, and a rule beginning with "!" is a rescue.
    """
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-v", "--no-index", str(path)],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return False                      # no rule matched at all
    rule = r.stdout.split("\t")[0].rsplit(":", 1)[-1]
    return not rule.startswith("!")


def test_the_ignore_check_can_actually_fail():
    """
    The guard below is only worth having if it detects a real exclusion, and the
    obvious way to write it does not. Anchored on a path the repository genuinely
    ignores.
    """
    assert ignored(ROOT / "review_queue.csv")
    assert not ignored(ROOT / "storage" / "load_dol.py")


@pytest.mark.parametrize("path", sorted(
    list((ROOT / "tests" / "fixtures").glob("*.csv"))
    + [ROOT / "storage" / "employer_aliases.csv"]
    + list((ROOT / "dbt_signal" / "seeds").glob("*.csv"))
    # Recorded agent runs are source for the /agent/ console, not pulled data.
    # The blanket data/ rule matched them, and an excluded recording renders an
    # empty console in CI while working perfectly on the machine that built it.
    + list((ROOT / "site" / "data").glob("*.json"))
), ids=lambda p: p.name)
def test_committed_data_files_are_not_gitignored(path):
    """
    A blanket *.csv rule has now swallowed three files that are source code
    rather than data: the technology seed, the employer alias decisions, and the
    labelled pairs the precision gate reads. Each was discovered only when
    whatever needed the file failed somewhere else, which is the expensive way.
    """
    assert not ignored(path), (
        f"{path.relative_to(ROOT)} is gitignored, so it will not reach CI or a "
        f"fresh checkout. Add an exception to .gitignore."
    )


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
