"""
Tests for the enrichment eval's scoring.

The harness decides whether a prompt change made things better or worse, so its
own arithmetic has to be right: a broken tolerance or a mis-paired diff would
report a regression that is not there, or hide one that is. All of this is pure
functions, so none of it needs a key, a network or a database.

The cases are the ones that actually matter in use - the tolerance boundary, a
missing answer, a change that is not a regression - rather than exhaustive
coverage of the obvious.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from eval_utils import (
    FIT_SCORE_TOLERANCE,
    REGRESSION,
    compare,
    grade_example,
    row_from_example,
    score_categorical,
    score_numeric,
    summarise,
    validate_example,
)

ROOT = Path(__file__).resolve().parents[1]


def example(**overrides) -> dict:
    base = {
        "job_id": "company_board:abc",
        "source": "company_board",
        "company": "Acme Data",
        "title": "Data Engineering Co-op",
        "country": "ca",
        "location": "Toronto, Ontario",
        "state": "Ontario",
        "posted_date": "2026-09-08",
        "seniority": "intern",
        "salary_min": None,
        "salary_is_predicted": None,
        "raw_posting_text": "Build pipelines with Python and dbt.",
        "expected_sponsorship_signal": "unclear",
        "expected_eligibility": "eligible",
        "expected_fit_score": 80,
        "notes": "",
    }
    return {**base, **overrides}


def actual(**overrides) -> dict:
    base = {"sponsorship_signal": "unclear", "eligibility": "eligible", "fit_score": 80}
    return {**base, **overrides}


class TestTheFitScoreTolerance:
    """Ten points, and the boundary is inclusive. This is the number to explain."""

    @pytest.mark.parametrize("got, expected_pass", [
        (80, True),    # exact
        (90, True),    # exactly the tolerance, inclusive
        (70, True),
        (91, False),   # one point past it
        (69, False),
    ])
    def test_the_boundary(self, got, expected_pass):
        assert score_numeric(80, got) is expected_pass

    def test_the_tolerance_is_ten(self):
        """Stated here too, so changing it has to be deliberate in two places."""
        assert FIT_SCORE_TOLERANCE == 10

    def test_a_band_change_fails(self):
        """45 to 75 is 'worth a look' becoming 'apply now' on the board."""
        assert not score_numeric(45, 75)

    def test_a_missing_score_is_a_failure_not_a_skip(self):
        assert not score_numeric(80, None)


class TestCategoricalScoring:
    def test_exact_match_only(self):
        assert score_categorical("blocked", "blocked")
        assert not score_categorical("blocked", "eligible")

    def test_a_missing_answer_is_wrong(self):
        """Never silently excluded from the denominator."""
        assert not score_categorical("unclear", None)


class TestGrading:
    def test_one_wrong_field_fails_the_example(self):
        """The board shows all three together, so two out of three is not a pass."""
        graded = grade_example(example(), actual(eligibility="blocked"))
        assert graded["eligibility"]["pass"] is False
        assert graded["sponsorship_signal"]["pass"] is True
        assert graded["passed_all"] is False

    def test_absolute_error_is_recorded_even_when_it_passes(self):
        graded = grade_example(example(), actual(fit_score=85))
        assert graded["fit_score"]["abs_error"] == 5
        assert graded["fit_score"]["pass"] is True


class TestSummary:
    def test_rates_and_mean_error(self):
        graded = [
            grade_example(example(job_id="a"), actual()),
            grade_example(example(job_id="b"), actual(fit_score=60)),   # off by 20
            grade_example(example(job_id="c"), actual(eligibility="blocked")),
        ]
        s = summarise(graded)
        assert s["examples"] == 3
        assert s["sponsorship_signal_accuracy_pct"] == 100.0
        assert s["eligibility_accuracy_pct"] == round(100 * 2 / 3, 1)
        assert s["fit_score_pass_rate_pct"] == round(100 * 2 / 3, 1)
        # (0 + 20 + 0) / 3
        assert s["fit_score_mean_absolute_error"] == round(20 / 3, 1)
        assert sorted(s["failed_job_ids"]) == ["b", "c"]
        assert s["all_passed"] is False

    def test_an_empty_set_does_not_divide_by_zero(self):
        assert summarise([])["examples"] == 0


class TestComparison:
    def test_a_pass_becoming_a_fail_is_a_regression(self):
        before = [grade_example(example(job_id="a"), actual())]
        after = [grade_example(example(job_id="a"), actual(eligibility="blocked"))]
        diff = compare(after, before)
        assert len(diff["regressions"]) == 1
        assert diff["regressions"][0]["field"] == "eligibility"
        assert diff["regressions"][0]["status"] == REGRESSION

    def test_a_changed_answer_that_still_passes_is_not_a_regression(self):
        """40 to 44 against an expected 42 changed, and is noise."""
        before = [grade_example(example(job_id="a", expected_fit_score=42),
                                actual(fit_score=40))]
        after = [grade_example(example(job_id="a", expected_fit_score=42),
                               actual(fit_score=44))]
        assert compare(after, before)["regressions"] == []

    def test_a_fail_becoming_a_pass_is_an_improvement(self):
        before = [grade_example(example(job_id="a"), actual(fit_score=10))]
        after = [grade_example(example(job_id="a"), actual())]
        diff = compare(after, before)
        assert not diff["regressions"] and len(diff["improvements"]) == 1

    def test_examples_present_in_only_one_run_are_reported(self):
        """A golden set that lost an example must not read as an improvement."""
        before = [grade_example(example(job_id="gone"), actual())]
        after = [grade_example(example(job_id="fresh"), actual())]
        statuses = {c["job_id"]: c["status"] for c in compare(after, before)["changes"]}
        assert statuses == {"fresh": "new", "gone": "dropped"}


class TestValidation:
    def test_a_good_example_has_no_problems(self):
        assert validate_example(example()) == []

    def test_an_unknown_category_is_caught(self):
        problems = validate_example(example(expected_eligibility="maybe"))
        assert any("expected_eligibility" in p for p in problems)

    @pytest.mark.parametrize("score", [-1, 101, "80", 80.5, True])
    def test_a_bad_fit_score_is_caught(self, score):
        assert validate_example(example(expected_fit_score=score))

    def test_a_missing_field_is_named(self):
        broken = example()
        del broken["raw_posting_text"]
        assert "missing raw_posting_text" in validate_example(broken)


class TestTheAdapter:
    def test_the_row_matches_what_assess_destructures(self):
        """
        assess() unpacks twelve values positionally, so order is the contract.
        A shift by one would score the wrong fields and still run.
        """
        row = row_from_example(example())
        assert len(row) == 12
        source, job_id, company, title, location, state, posted, seniority, \
            description, salary_min, salary_predicted, country = row
        assert (source, job_id, company) == ("company_board", "company_board:abc", "Acme Data")
        assert description == "Build pipelines with Python and dbt."
        assert country == "ca"
        assert seniority == "intern"

    def test_the_real_enrichment_signature_still_has_twelve_fields(self):
        """
        Read from ai_layer/enrich.py as text, so this fails when assess() changes
        shape rather than when a paid run produces nonsense.
        """
        src = (ROOT / "ai_layer" / "enrich.py").read_text()
        block = src[src.index("def assess("):]
        unpack = block[block.index("(_src"):block.index("= row")]
        assert len([p for p in unpack.replace("\n", " ").strip(" (),").split(",")]) == 12
