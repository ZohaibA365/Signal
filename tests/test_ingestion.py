"""Tests for the Adzuna ingestion layer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))

from adzuna_ingest import s3_key


def test_s3_key_uses_hive_partitioning():
    """Glue, Athena, and Spark rely on the key=value directory convention."""
    key = s3_key("data engineer", 1, "2026-08-23")
    assert key == "raw/source=adzuna/ingest_date=2026-08-23/data_engineer__page01.json"


def test_s3_key_zero_pads_page_numbers():
    """Zero padding keeps pages in lexical order in listings."""
    assert "page02" in s3_key("data engineer", 2, "2026-08-23")
    assert "page10" in s3_key("data engineer", 10, "2026-08-23")


def test_s3_key_slugs_multiword_terms():
    """Spaces in an S3 key are legal but awkward; the term becomes a slug."""
    assert "machine_learning_engineer" in s3_key("machine learning engineer", 1, "2026-08-23")


def test_partitions_differ_by_date():
    a = s3_key("data engineer", 1, "2026-08-23")
    b = s3_key("data engineer", 1, "2026-08-24")
    assert a != b
