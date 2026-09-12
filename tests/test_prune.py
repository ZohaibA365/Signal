"""
Tests for the warehouse retention rules.

These are text assertions over SQL rather than behavioural tests against a
database, because the two failures worth catching here are both failures of
omission in SQL that would still run.

Dropping description text only works while the loader refuses to put it back:
the postings whose text is dropped are still open, still in the feed, and still
re-upserted every morning with the full description attached. A rewrite of the
UPSERT that loses one CASE branch would restore 260 MB overnight and nothing
would error. And text is only ever dropped once S3 provably holds that exact
text, which is a join the drop cannot be missing.

The database-level behaviour is covered by running the thing: prune reports what
it would do before it does it, and scripts/ci_rehearse.sh builds the schema from
storage/schema.sql on every run.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storage"))

import prune
from load_to_warehouse import UPSERT

ROOT = Path(__file__).resolve().parents[1]


class TestLoaderDoesNotRestoreDroppedText:
    def test_upsert_checks_the_marker_before_assigning_text(self):
        assert "description_dropped_at IS NOT NULL THEN NULL" in UPSERT

    def test_the_marker_is_checked_before_the_unchanged_shortcut(self):
        """Order matters: the later branch would assign the incoming text."""
        marker = UPSERT.index("description_dropped_at IS NOT NULL")
        unchanged = UPSERT.index("IS NOT DISTINCT FROM EXCLUDED.description_raw")
        assert marker < unchanged

    def test_the_column_is_declared_in_the_schema(self):
        """schema.sql is the single source of truth, and CI applies only it."""
        schema = (ROOT / "storage" / "schema.sql").read_text()
        assert "description_dropped_at" in schema


class TestRetentionPredicate:
    def test_keeps_recent_arrivals_by_first_seen(self):
        """Not ingested_at: the UPSERT refreshes it, so everything looks new."""
        assert "r.first_seen >" in prune.KEEP_TEXT
        assert "ingested_at" not in prune.KEEP_TEXT

    def test_keeps_roles_the_scorer_has_not_reached(self):
        assert "job_enrichment" in prune.KEEP_TEXT
        assert "'intern'" in prune.KEEP_TEXT and "'entry'" in prune.KEEP_TEXT

    def test_window_is_parameterised(self):
        assert "%(keep_days)s" in prune.KEEP_TEXT
        assert prune.KEEP_DAYS >= 7


class TestDropRequiresTheArchive:
    def test_the_drop_joins_the_archive_on_the_hash(self):
        """Membership by (source, job_id) alone would not prove the text matches."""
        source = (ROOT / "storage" / "prune.py").read_text()
        body = source[source.index("CREATE TEMP TABLE to_drop"):]
        body = body[: body.index('"""')]
        assert "JOIN archived_text" in body
        assert "a.sha =" in body

    def test_empty_descriptions_are_left_alone(self):
        """The archive skips them, so they can never satisfy the membership test."""
        source = (ROOT / "storage" / "prune.py").read_text()
        assert "r.description_raw <> ''" in source

    def test_without_a_bucket_nothing_is_dropped(self, monkeypatch):
        monkeypatch.delenv("S3_BUCKET", raising=False)

        class Fail:
            def execute(self, *a, **k):
                raise AssertionError("prune touched the database with no archive")

        args = type("Args", (), {"apply": True, "keep_days": 7})()
        assert prune.drop_text(Fail(), args) == (0, 0.0)

    def test_batches_are_small_enough_to_vacuum_between(self):
        """One statement over every description is what filled the database."""
        assert prune.TEXT_BATCH <= 5_000
        assert prune.VACUUM_EVERY >= 1

    def test_the_loop_consumes_its_candidate_table(self):
        """Without the DELETE ... RETURNING the batch loop would never end."""
        source = (ROOT / "storage" / "prune.py").read_text()
        loop = source[source.index("cleared = 0"):]
        assert re.search(r"DELETE FROM to_drop", loop)
        assert "RETURNING source, job_id" in loop
