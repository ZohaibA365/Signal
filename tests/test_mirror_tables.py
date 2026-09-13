"""
Every mirror must carry every table dbt reads.

Two engines are now mirrored from Postgres for parity testing, and a table
missing from the list does not fail loudly: the build reports a Database Error on
that source's tests and then SKIPS every model downstream. The first full
Snowflake build did exactly that - 44 passed, 4 errored, 65 skipped - so almost
nothing was verified while the run looked like it had mostly worked.

A skipped model is the dangerous outcome. A failing one announces itself; a
skipped one just quietly is not tested, and testing things is the entire purpose
of the extra targets.

The list therefore lives in storage/mirror_tables.py and both loaders import it,
so there is one thing to keep right rather than two that can drift. These tests
check the list against what dbt actually declares, and check that both loaders
still read it from the shared place.

Pure text comparison, no database and no network, so it runs in CI where neither
warehouse nor either driver is available.
"""

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHARED = ROOT / "storage" / "mirror_tables.py"
LOADERS = (ROOT / "storage" / "load_to_snowflake.py",
           ROOT / "storage" / "load_to_databricks.py")
MODELS = ROOT / "dbt_signal" / "models"


def mirrored_tables() -> set[str]:
    """The shared TABLES list, read as text rather than imported."""
    src = SHARED.read_text()
    block = src[src.index("TABLES = ["):]
    block = block[:block.index("]")]
    return set(re.findall(r'"(\w+)"', block))


def source_tables() -> dict[str, str]:
    found = {}
    for path in MODELS.rglob("*.yml"):
        doc = yaml.safe_load(path.read_text()) or {}
        for src in doc.get("sources", []) or []:
            for table in src.get("tables", []) or []:
                found[table["name"]] = str(path.relative_to(ROOT))
    return found


def test_every_dbt_source_is_mirrored():
    missing = {t: f for t, f in source_tables().items() if t not in mirrored_tables()}
    assert not missing, (
        "dbt reads these as sources but storage/mirror_tables.py does not list "
        "them, so a build on Snowflake or Databricks errors on their tests and "
        f"silently skips every model downstream: {missing}"
    )


@pytest.mark.parametrize("loader", LOADERS, ids=lambda p: p.name)
def test_both_loaders_use_the_shared_list(loader):
    """
    Neither may keep its own copy. Two hand-written lists of the same thing is
    exactly how company_identity went missing from one of them.
    """
    src = loader.read_text()
    assert "from mirror_tables import" in src and "TABLES" in src
    assert "TABLES = [" not in src, f"{loader.name} defines its own TABLES list"


@pytest.mark.parametrize("table", sorted(source_tables()))
def test_source_table_named_individually(table):
    """Parametrised so a failure names the table in the test id."""
    assert table in mirrored_tables()


def test_the_mirror_does_not_copy_models():
    """
    Only SOURCE tables are copied; every model is rebuilt by dbt in Snowflake.
    Shipping a model across would make the target prove nothing - it would be
    comparing a copy of the Postgres answer against itself rather than
    recomputing it on a second engine.
    """
    models = {p.stem for p in MODELS.rglob("*.sql")}
    leaked = mirrored_tables() & models
    assert not leaked, f"these are dbt models, not sources: {leaked}"
