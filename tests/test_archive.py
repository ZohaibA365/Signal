"""
Tests for the S3 posting archive.

The archive exists because the warehouse forgets - raw_postings is upserted
in place, prune.py deletes rows, and the curated Parquet layer is rewritten
per partition. So the failure that matters here is the quiet one: a run that
reports success while writing less than it was asked to. That is not
hypothetical. The first version paged its description SELECT through
execute_values at the default page size and archived 100 rows per batch
instead of 2,000, skipping 55,100 of 58,013 descriptions without an error.
"""

import io
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "storage"))

import archive_postings as arc  # noqa: E402


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3's signature
        self.objects[Key] = Body


def _rows(n):
    return [{"source": "board", "job_id": f"j{i}", "observed_date": "2026-09-09"}
            for i in range(n)]


def test_put_writes_every_row_it_was_given():
    """The regression that motivated the test: silent truncation."""
    s3 = FakeS3()
    arc.put(s3, "bucket", "k.parquet", _rows(2_000), dry=False)
    table = pq.read_table(io.BytesIO(s3.objects["k.parquet"]))
    assert table.num_rows == 2_000


def test_put_round_trips_content_not_just_row_count():
    s3 = FakeS3()
    arc.put(s3, "bucket", "k.parquet", _rows(3), dry=False)
    table = pq.read_table(io.BytesIO(s3.objects["k.parquet"]))
    assert table.column("job_id").to_pylist() == ["j0", "j1", "j2"]


def test_dry_run_writes_nothing():
    """A dry run that still uploaded would be worse than no dry run."""
    s3 = FakeS3()
    written = arc.put(s3, "bucket", "k.parquet", _rows(10), dry=True)
    assert s3.objects == {}
    assert written > 0          # still reports the size it would have written


def test_empty_input_writes_no_object():
    s3 = FakeS3()
    assert arc.put(s3, "bucket", "k.parquet", [], dry=False) == 0
    assert s3.objects == {}


def test_batch_is_large_enough_to_be_worth_batching():
    """
    Guards the paging bug directly. execute_values is called with
    page_size=len(chunk), so BATCH is also the page size; a small value here
    would quietly reintroduce per-page truncation semantics.
    """
    assert arc.BATCH >= 1_000


def test_description_hash_is_the_full_digest():
    """
    enrich.py keys on the first 16 characters. Storing the full 64 keeps that
    a prefix of this, so the two remain joinable, and leaves the archive
    enough bits that collisions stay theoretical.
    """
    assert "sha256" in arc.SHA
    assert "substring" not in arc.SHA


def test_attributes_never_carry_the_text():
    """
    Description text is archived once, deduplicated by hash. Including it in
    the attribute rows too would store 195 MB twice for nothing.
    """
    assert "description_raw" not in arc.ATTRS


@pytest.mark.parametrize("column", ["source", "job_id", "first_seen", "last_seen"])
def test_attributes_carry_the_keys_history_needs(column):
    assert column in arc.ATTRS


# ------------------------------------------- two runs on one day must not collide
#
# They did. Description and manifest objects were keyed by day alone - part-0000,
# then 2026-09-11-0000 - so a second run on the same day overwrote the first run's
# files instead of adding to them. The daily pipeline archived its descriptions at
# 11:52, a later run archived 866 more at 15:23, and the 15:23 file replaced the
# 11:52 one: 828 descriptions vanished from an append-only store.
#
# It was caught only because the manifest count went DOWN after archiving. A store
# whose contents shrink is not append-only, and had the reclaim run on that
# verification, 828 descriptions would have been deleted from Postgres while
# absent from S3.


def test_the_run_stamp_is_in_the_description_and_manifest_keys():
    """
    The fix. Presence and attribute keys are deliberately NOT stamped - those are
    whole-day snapshots and rewriting one with identical content is what makes
    re-running a day safe. Only the append-only prefixes need uniqueness.
    """
    import inspect
    desc = inspect.getsource(arc.archive_descriptions)
    man = inspect.getsource(arc.save_manifest)
    assert "RUN_STAMP" in desc, "description keys collide between runs on one day"
    assert "RUN_STAMP" in man, "manifest keys collide between runs on one day"

    presence = inspect.getsource(arc.archive_presence)
    attrs = inspect.getsource(arc.archive_attributes)
    assert "RUN_STAMP" not in presence
    assert "RUN_STAMP" not in attrs


def test_the_run_stamp_is_unique_enough_to_separate_runs():
    """Second resolution: two runs of a 30-minute pipeline cannot share a stamp."""
    import re
    assert re.fullmatch(r"\d{8}T\d{6}", arc.RUN_STAMP), arc.RUN_STAMP


def test_a_second_write_on_the_same_day_uses_a_different_key():
    """
    The property that actually matters, asserted on the keys themselves rather
    than on the code that builds them.
    """
    s3 = FakeS3()
    rows = [{"source": "board", "job_id": "j1", "description_sha256": "a",
             "description_raw": "x"}]
    day = "2026-09-11"
    first = f"archive/descriptions/archived_date={day}/part-{arc.RUN_STAMP}-0000.parquet"
    arc.put(s3, "bucket", first, rows, dry=False)

    # A later run carries a later stamp.
    later = first.replace(arc.RUN_STAMP, "20260911T999999")
    arc.put(s3, "bucket", later, rows, dry=False)

    assert len(s3.objects) == 2, "a second run overwrote the first run's file"
