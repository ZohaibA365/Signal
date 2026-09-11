"""
Every table a dbt model reads must be declared in storage/schema.sql.

schema.sql says this about itself: it is the single source of truth for
structure, CI applies it and then builds dbt, so a table a model reads and this
file does not declare fails the build on a missing relation. Scripts only load.

That rule has now been broken three times, each time discovered in CI rather
than locally, because a laptop's warehouse already has the table from whenever
the script that creates it last ran:

  - technologies.csv was excluded by a blanket *.csv rule while its _seeds.yml
    was committed, so CI found a declaration with no data
  - employer_aliases.csv went the same way, and the aliases silently did nothing
    outside the laptop
  - company_identity was declared only in the DDL of the two scripts that write
    it, so three source tests failed on a missing relation

A pure-text check catches all of it in a second, with no database and no
network, which is the difference between noticing before pushing and noticing
after.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "storage" / "schema.sql"
MODELS = ROOT / "dbt_signal" / "models"
SEED_FIXTURE = ROOT / "tests" / "fixtures" / "seed_ci.sql"


def declared_tables() -> set[str]:
    sql = SCHEMA.read_text()
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE))


def source_tables() -> dict[str, str]:
    """Every table named in a dbt source yml, mapped to the file declaring it."""
    found: dict[str, str] = {}
    for path in MODELS.rglob("*.yml"):
        doc = yaml.safe_load(path.read_text()) or {}
        for src in doc.get("sources", []) or []:
            for table in src.get("tables", []) or []:
                found[table["name"]] = str(path.relative_to(ROOT))
    return found


def seeded_tables() -> set[str]:
    sql = SEED_FIXTURE.read_text()
    return {t.lower() for t in re.findall(r"INSERT INTO\s+(\w+)", sql, re.IGNORECASE)}


def test_every_source_table_is_declared_in_schema_sql():
    declared = {t.lower() for t in declared_tables()}
    missing = {t: f for t, f in source_tables().items() if t.lower() not in declared}
    assert not missing, (
        "dbt sources reference tables that storage/schema.sql does not declare, "
        "so CI will fail on a missing relation even though a local warehouse has "
        f"them already: {missing}"
    )


def test_every_source_table_is_seeded_for_ci():
    """
    Declared is not enough. An empty source table passes the build but makes
    every downstream assertion vacuous - the dbt models would compile against
    nothing and the behavioural checks would pass on zero rows.
    """
    seeded = seeded_tables()
    unseeded = sorted(t for t in source_tables() if t.lower() not in seeded)
    assert not unseeded, (
        "dbt source tables with no rows in tests/fixtures/seed_ci.sql, so the CI "
        f"build exercises them empty: {unseeded}"
    )


@pytest.mark.parametrize("table", sorted(source_tables()))
def test_source_table_named_individually(table):
    """
    Parametrised so a failure names the offending table in the test id rather
    than inside an assertion message.
    """
    assert table.lower() in {t.lower() for t in declared_tables()}
