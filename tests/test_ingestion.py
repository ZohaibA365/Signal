"""Tests for the Adzuna ingestion layer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))

from adzuna_ingest import s3_key


def test_s3_key_uses_hive_partitioning():
    """
    Glue, Athena and Spark rely on the key=value directory convention.

    country is part of the path because salary is quoted in local currency:
    mixing US and Canadian postings in one partition would make any aggregate
    over salary silently wrong.
    """
    key = s3_key("data engineer", 1, "2026-08-23")
    assert key == ("raw/source=adzuna/country=us/ingest_date=2026-08-23/"
                   "data_engineer__page001.json")


def test_s3_key_separates_countries():
    us = s3_key("data engineer", 1, "2026-08-23", "us")
    ca = s3_key("data engineer", 1, "2026-08-23", "ca")
    assert "country=us" in us and "country=ca" in ca
    assert us != ca


def test_s3_key_zero_pads_page_numbers():
    """
    Three digits, not two: deep backfills reach page 100+, and two-digit
    padding would sort page100 before page11 in a listing.
    """
    assert "page002" in s3_key("data engineer", 2, "2026-08-23")
    assert "page010" in s3_key("data engineer", 10, "2026-08-23")
    assert "page100" in s3_key("data engineer", 100, "2026-08-23")


def test_s3_key_slugs_multiword_terms():
    """Spaces in an S3 key are legal but awkward; the term becomes a slug."""
    assert "machine_learning_engineer" in s3_key("machine learning engineer", 1, "2026-08-23")


def test_partitions_differ_by_date():
    a = s3_key("data engineer", 1, "2026-08-23")
    b = s3_key("data engineer", 1, "2026-08-24")
    assert a != b


# ------------------------------------------- resilience of the fetch/retry path
#
# The scheduled run on 2026-09-10 failed at this step 95 seconds in and took
# fourteen steps down with it, including the archive, whose day cannot be
# recreated once the next load moves last_seen. One bad call aborted every
# remaining search term and the second country. These tests pin the behaviour
# that replaced that.

import adzuna_ingest as adz  # noqa: E402
import pytest  # noqa: E402


class FakeResponse:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _patch_get(monkeypatch, responses):
    """Serve the given responses in order, recording how many calls were made."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(adz.requests, "get", fake_get)
    monkeypatch.setattr(adz.time, "sleep", lambda _s: None)
    return calls


def test_403_is_retried_then_succeeds(monkeypatch):
    """
    Adzuna publishes no rate limits and this pipeline makes ~300 calls a day, so
    a refusal partway through is far more likely throttling than a bad key.
    Before, 403 raised on the first response and killed the step.
    """
    calls = _patch_get(monkeypatch, [
        FakeResponse(403, text="throttled"),
        FakeResponse(200, payload={"results": [{"id": "1"}], "count": 1}),
    ])
    payload = adz.fetch_page("data engineer", 1, "id", "key")
    assert payload["results"] == [{"id": "1"}]
    assert len(calls) == 2


def test_retry_after_header_is_honoured(monkeypatch):
    """The server knows its own throttle better than an exponential guess."""
    slept = []
    monkeypatch.setattr(adz.time, "sleep", slept.append)
    monkeypatch.setattr(adz.requests, "get", lambda *a, **k: FakeResponse(
        429, text="slow down", headers={"Retry-After": "7"}))
    with pytest.raises(adz.FetchFailed):
        adz.fetch_page("data engineer", 1, "id", "key")
    assert 7 in slept


def test_retry_after_is_capped(monkeypatch):
    """A hostile or broken Retry-After must not park the run for an hour."""
    slept = []
    monkeypatch.setattr(adz.time, "sleep", slept.append)
    monkeypatch.setattr(adz.requests, "get", lambda *a, **k: FakeResponse(
        429, headers={"Retry-After": "99999"}))
    with pytest.raises(adz.FetchFailed):
        adz.fetch_page("data engineer", 1, "id", "key")
    assert max(slept) <= 60


def test_non_retryable_status_carries_the_code(monkeypatch):
    """
    The status is the whole diagnosis. The old RuntimeError buried it in a
    message, so the only thing that escaped the run was "exit code 1".
    """
    _patch_get(monkeypatch, [FakeResponse(400, text="bad request")])
    with pytest.raises(adz.FetchFailed) as exc:
        adz.fetch_page("data engineer", 1, "id", "key")
    assert exc.value.status == 400
    assert "400" in exc.value.detail


def test_network_error_is_retried(monkeypatch):
    attempts = []

    def fake_get(*_a, **_k):
        attempts.append(1)
        if len(attempts) < 2:
            raise adz.requests.ConnectionError("reset by peer")
        return FakeResponse(200, payload={"results": [], "count": 0})

    monkeypatch.setattr(adz.requests, "get", fake_get)
    monkeypatch.setattr(adz.time, "sleep", lambda _s: None)
    assert adz.fetch_page("data engineer", 1, "id", "key") == {"results": [], "count": 0}


# ------------------------------------------------------- the exit-code contract


class _Args:
    dry_run = True


def test_partial_failure_does_not_fail_the_run():
    """
    Fourteen terms succeeding and one failing is a warning, not a dead run.
    Exiting non-zero here is what threw away work already done.
    """
    adz.report(total_postings=500, files_written=0, skipped=0, planned=10,
               failures=[("us", "data engineer", 3, 403, "HTTP 403")], args=_Args())


def test_total_failure_does_fail_the_run():
    """Nothing collected and nothing already present is a real outage."""
    with pytest.raises(SystemExit) as exc:
        adz.report(total_postings=0, files_written=0, skipped=0, planned=10,
                   failures=[("us", "data engineer", 1, 401, "HTTP 401")], args=_Args())
    assert exc.value.code == 1


def test_fully_resumed_run_is_not_a_failure():
    """
    Every page already present is the --resume no-op that made three runs look
    green in 6 to 9 seconds. It is not an error, but report() must say so
    rather than leaving it indistinguishable from real work.
    """
    adz.report(total_postings=0, files_written=0, skipped=150, planned=150,
               failures=[], args=_Args())


def test_403_is_in_the_retryable_set():
    assert 403 in adz.RETRYABLE
    assert 429 in adz.RETRYABLE
