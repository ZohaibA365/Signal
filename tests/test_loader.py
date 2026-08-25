"""
Tests for the S3 -> Postgres transformation.

These cover the parsing quirks that actually bit during the build: Adzuna
returns numbers as strings, booleans as "0"/"1", and buries the state inside
a nested area array.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storage"))

from load_to_warehouse import _bool, _num, _state, flatten


class TestNumericParsing:
    def test_parses_numeric_strings(self):
        assert _num("120000") == 120000.0

    def test_passes_through_numbers(self):
        assert _num(95000.5) == 95000.5

    def test_empty_and_none_become_none(self):
        assert _num(None) is None
        assert _num("") is None

    def test_garbage_becomes_none_rather_than_raising(self):
        """One malformed salary must not abort a 900-row load."""
        assert _num("negotiable") is None


class TestBooleanParsing:
    def test_string_one_is_true(self):
        assert _bool("1") is True

    def test_string_zero_is_false(self):
        assert _bool("0") is False

    def test_missing_is_none_not_false(self):
        """None means 'unknown', which is different from 'not predicted'."""
        assert _bool(None) is None


class TestStateExtraction:
    def test_reads_state_from_area_hierarchy(self):
        posting = {"location": {"area": ["US", "Tennessee", "Davidson County", "Nashville"]}}
        assert _state(posting) == "Tennessee"

    def test_short_area_yields_none(self):
        assert _state({"location": {"area": ["US"]}}) is None

    def test_missing_location_yields_none(self):
        assert _state({}) is None


class TestFlatten:
    def _posting(self):
        return {
            "id": "5123456789",
            "title": "Data Engineer Intern",
            "company": {"display_name": "Acme Corp"},
            "location": {"display_name": "Nashville, Davidson County",
                         "area": ["US", "Tennessee", "Davidson County", "Nashville"]},
            "created": "2026-08-20T10:00:00Z",
            "salary_min": "90000", "salary_max": "110000",
            "salary_is_predicted": "1",
            "description": "Build data pipelines.",
            "category": {"label": "IT Jobs"},
            "redirect_url": "https://example.com/job/1",
            "latitude": 36.16, "longitude": -86.78,
        }

    def _meta(self):
        return {"source": "adzuna", "search_term": "data engineer intern",
                "ingested_at": "2026-08-23T06:00:00Z"}

    def test_maps_core_fields(self):
        row = flatten(self._posting(), self._meta(), "2026-08-23T07:00:00Z")
        assert row[0] == "adzuna"
        assert row[1] == "5123456789"
        assert row[2] == "Acme Corp"
        assert row[3] == "Data Engineer Intern"

    def test_job_id_is_string_even_when_numeric(self):
        """job_id is half the primary key; a type flip would break upserts."""
        row = flatten(self._posting(), self._meta(), "2026-08-23T07:00:00Z")
        assert isinstance(row[1], str)

    def test_handles_missing_nested_objects(self):
        """Adzuna sometimes omits company or location entirely."""
        bare = {"id": "1", "title": "Data Engineer"}
        row = flatten(bare, self._meta(), "2026-08-23T07:00:00Z")
        assert row[2] is None      # company_name
        assert row[4] is None      # location

    def test_predicted_salary_flag_survives_as_boolean(self):
        row = flatten(self._posting(), self._meta(), "2026-08-23T07:00:00Z")
        assert row[8] is True
