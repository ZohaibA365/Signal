"""
Scoring for the enrichment eval: pure functions, no I/O, no API calls.

Everything here is a function of its arguments, which is deliberate. It makes the
scoring unit-testable without a key or a network, and it is the seam a Great
Expectations expectation or an Airflow task would call later without dragging the
console-printing code along with it.

The vocabularies below are the real ones the model returns, declared in
ai_layer/enrich.py's Assessment model. They are NOT "yes/no/unclear": sponsorship
and eligibility are two different judgements, and the prompt says so. A posting
whose text is silent about work authorisation at a defence contractor should come
back as sponsorship_signal 'unclear' AND eligibility 'blocked' - the first reports
only what the text states, the second is allowed to use what is known about the
employer. An eval that collapsed them into one field would mark correct behaviour
wrong.
"""

from __future__ import annotations

# ---------------------------------------------------------------- the thresholds

# How close a fit_score has to be to count as agreement.
#
# Exact match is the wrong test for this field and would make the eval useless.
# fit_score is a 0-100 judgement from a language model with no temperature pinned,
# so two runs of an unchanged prompt disagree by a few points on their own. The
# prompt does not even ask for a precise number - it asks for calibrated bands:
# "most postings are mediocre and should score in the 20-50 range", "reserve
# scores above 80 for genuinely strong matches".
#
# So the question worth asking is "did the judgement move bands", not "did the
# number change". Ten points is the width that answers it: inside a band, noise
# passes; a posting that moves from 45 to 75 - worth a look to apply now, which is
# what the reader of the board sees - fails. A tolerance of 25 would swallow that
# move entirely, and exact match would fail on nothing but noise.
FIT_SCORE_TOLERANCE = 10

# The two categorical fields are scored on EXACT match, with no partial credit.
# There is no sensible "close" between 'blocked' and 'eligible': one of them tells
# the candidate not to bother applying.
SPONSORSHIP_VALUES = ("sponsors", "no_sponsorship", "unclear")
ELIGIBILITY_VALUES = ("eligible", "blocked", "unclear")
CATEGORICAL_FIELDS = {
    "sponsorship_signal": SPONSORSHIP_VALUES,
    "eligibility": ELIGIBILITY_VALUES,
}

# Every key an example must carry. Kept here so a malformed golden set fails with
# a list of what is missing rather than a KeyError twenty lines into a paid run.
REQUIRED_FIELDS = (
    "job_id", "source", "company", "title", "country", "raw_posting_text",
    "expected_sponsorship_signal", "expected_eligibility", "expected_fit_score",
)


def validate_example(example: dict) -> list[str]:
    """Everything wrong with one golden-set entry, as plain sentences."""
    problems = []
    for field in REQUIRED_FIELDS:
        if field not in example or example[field] in (None, ""):
            problems.append(f"missing {field}")

    for field, allowed in CATEGORICAL_FIELDS.items():
        value = example.get(f"expected_{field}")
        if value is not None and value not in allowed:
            problems.append(f"expected_{field} is {value!r}, not one of {list(allowed)}")

    score = example.get("expected_fit_score")
    if isinstance(score, bool) or not isinstance(score, int):
        problems.append("expected_fit_score must be a whole number")
    elif not 0 <= score <= 100:
        problems.append(f"expected_fit_score {score} is outside 0-100")

    return problems


# ------------------------------------------------------- adapting to the enricher

def row_from_example(example: dict) -> tuple:
    """
    Build the tuple ai_layer.enrich.assess() destructures.

    assess() takes a row positionally, in the order the marts query selects it:

        (source, job_id, company, title, location, state, posted, seniority,
         description, salary_min, salary_predicted, country)

    This adapter is the ONLY place the golden set's shape meets the enrichment
    function's shape, so if assess() ever changes its signature there is exactly
    one thing to fix, and the eval fails loudly rather than silently scoring
    fields that have shifted by one position.
    """
    return (
        example["source"],
        example["job_id"],
        example["company"],
        example["title"],
        example.get("location"),
        example.get("state"),
        example.get("posted_date"),
        example.get("seniority"),
        example["raw_posting_text"],
        example.get("salary_min"),
        example.get("salary_is_predicted"),
        example["country"],
    )


# -------------------------------------------------------------------- the scoring

def score_categorical(expected: str, actual: str | None) -> bool:
    """Exact match. A missing answer is a wrong answer, never a skipped one."""
    return actual is not None and expected == actual


def score_numeric(expected: int, actual: int | None,
                  tolerance: int = FIT_SCORE_TOLERANCE) -> bool:
    """Within FIT_SCORE_TOLERANCE points. See the constant for why ten."""
    if actual is None:
        return False
    return abs(actual - expected) <= tolerance


def grade_example(example: dict, actual: dict) -> dict:
    """
    One example's result: expected against actual, field by field.

    Per-example rather than aggregate-only, because the aggregate cannot tell you
    which posting broke or how. `actual` is a plain dict so this never needs to
    import the enrichment module or pydantic.
    """
    result = {
        "job_id": example["job_id"],
        "company": example.get("company"),
        "title": example.get("title"),
        "notes": example.get("notes", ""),
    }

    for field in CATEGORICAL_FIELDS:
        expected = example[f"expected_{field}"]
        got = actual.get(field)
        result[field] = {
            "expected": expected,
            "actual": got,
            "pass": score_categorical(expected, got),
        }

    expected_score = example["expected_fit_score"]
    got_score = actual.get("fit_score")
    result["fit_score"] = {
        "expected": expected_score,
        "actual": got_score,
        "abs_error": None if got_score is None else abs(got_score - expected_score),
        "pass": score_numeric(expected_score, got_score),
        "tolerance": FIT_SCORE_TOLERANCE,
    }

    # One posting "passes" only when every judgement about it does. The board
    # shows all three together, so a row that is right about sponsorship and
    # wrong about eligibility is not a half-success to whoever reads it.
    result["passed_all"] = all(result[f]["pass"]
                               for f in (*CATEGORICAL_FIELDS, "fit_score"))
    return result


def summarise(per_example: list[dict]) -> dict:
    """Accuracy per categorical field, pass rate and mean error for fit_score."""
    n = len(per_example)
    if not n:
        return {"examples": 0, "all_passed": True, "failed_job_ids": []}

    summary: dict = {"examples": n}
    for field in CATEGORICAL_FIELDS:
        correct = sum(1 for r in per_example if r[field]["pass"])
        summary[f"{field}_correct"] = correct
        summary[f"{field}_accuracy_pct"] = round(100 * correct / n, 1)

    passed = sum(1 for r in per_example if r["fit_score"]["pass"])
    errors = [r["fit_score"]["abs_error"] for r in per_example
              if r["fit_score"]["abs_error"] is not None]
    summary["fit_score_passed"] = passed
    summary["fit_score_pass_rate_pct"] = round(100 * passed / n, 1)
    # Mean absolute error is reported alongside the pass rate because they answer
    # different questions. The pass rate says how often the judgement was usable;
    # the mean error says how far off it was when it missed, which is what tells
    # you whether a prompt change nudged the scale or broke it.
    summary["fit_score_mean_absolute_error"] = (
        round(sum(errors) / len(errors), 1) if errors else None
    )
    summary["fit_score_tolerance"] = FIT_SCORE_TOLERANCE

    summary["failed_job_ids"] = [r["job_id"] for r in per_example if not r["passed_all"]]
    summary["all_passed"] = not summary["failed_job_ids"]
    return summary


# ----------------------------------------------------------------- run-to-run diff

# What a field did between two runs. Only REGRESSION is a problem, and naming the
# others explicitly keeps the diff readable rather than a wall of unchanged rows.
REGRESSION = "regression"
IMPROVEMENT = "improvement"
UNCHANGED = "unchanged"
ADDED = "new"
REMOVED = "dropped"

SCORED_FIELDS = (*CATEGORICAL_FIELDS, "fit_score")


def compare(current: list[dict], previous: list[dict]) -> dict:
    """
    Diff two runs, field by field, paired on job_id.

    A regression is specifically a field that passed before and fails now. That
    is narrower than "the answer changed": a fit_score moving 40 to 44 against an
    expected 42 changed and is not a regression, and reporting it as one would
    train you to ignore the output.

    Examples present in only one run are reported as added or dropped rather than
    quietly skipped, because a golden set that lost an example would otherwise
    look like an improvement.
    """
    now = {r["job_id"]: r for r in current}
    before = {r["job_id"]: r for r in previous}

    changes = []
    for job_id in sorted(now.keys() & before.keys()):
        for field in SCORED_FIELDS:
            was, is_now = before[job_id][field], now[job_id][field]
            if was["pass"] and not is_now["pass"]:
                status = REGRESSION
            elif not was["pass"] and is_now["pass"]:
                status = IMPROVEMENT
            else:
                status = UNCHANGED
            if status != UNCHANGED:
                changes.append({
                    "job_id": job_id, "field": field, "status": status,
                    "was": was["actual"], "now": is_now["actual"],
                    "expected": is_now["expected"],
                })

    for job_id in sorted(now.keys() - before.keys()):
        changes.append({"job_id": job_id, "field": "-", "status": ADDED,
                        "was": None, "now": None, "expected": None})
    for job_id in sorted(before.keys() - now.keys()):
        changes.append({"job_id": job_id, "field": "-", "status": REMOVED,
                        "was": None, "now": None, "expected": None})

    return {
        "changes": changes,
        "regressions": [c for c in changes if c["status"] == REGRESSION],
        "improvements": [c for c in changes if c["status"] == IMPROVEMENT],
        "compared": len(now.keys() & before.keys()),
    }
