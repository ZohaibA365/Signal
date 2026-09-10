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
