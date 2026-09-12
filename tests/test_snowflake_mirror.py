"""
The Snowflake mirror must carry every table dbt reads.

storage/load_to_snowflake.py keeps a hand-written list of source tables to copy.
A table missing from it does not fail loudly: the Snowflake build reports a
Database Error on that source's tests and then SKIPS every model downstream. The
first full build did exactly that - 44 passed, 4 errored, 65 skipped - so almost
nothing was actually verified while the run looked like it had mostly worked.

A skipped model is the dangerous outcome. A failing one announces itself; a
skipped one just quietly is not tested, and the entire purpose of the Snowflake
target is to test things.

Pure text comparison, no database and no network, so it runs in CI where neither
Snowflake nor its driver is available.
"""

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOADER = ROOT / "storage" / "load_to_snowflake.py"
MODELS = ROOT / "dbt_signal" / "models"


def mirrored_tables() -> set[str]:
    """The TABLES list in the loader, read as text rather than imported."""
    src = LOADER.read_text()
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


def test_every_dbt_source_is_mirrored_to_snowflake():
    missing = {t: f for t, f in source_tables().items() if t not in mirrored_tables()}
    assert not missing, (
        "dbt reads these as sources but load_to_snowflake.py does not copy them, "
        "so a Snowflake build errors on their tests and silently skips every "
        f"model downstream: {missing}"
    )


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
