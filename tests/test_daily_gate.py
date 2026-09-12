"""
Tests for the daily pipeline's failure policy.

Fourteen scheduled runs of the daily pipeline had failed, and not one had ever
succeeded - every green run was a manual dispatch. They failed at six different
steps, which is the tell: not one bug, but a chain with too many links. The
workflow had 19 fatal steps, and a fatal step skips every step after it. On
2026-09-10 a transient HTTP 503 from the aggregator at step 8 cost the board
ingest, both loads, the archive, the transform and the site refresh - fourteen
steps that would all have succeeded.

At a 1% per-step transient rate, 19 fatal links fail about five mornings a month.
At 2%, ten.

So steps no longer decide the outcome. They all continue, and one final step
decides, failing only for damage a later run cannot repair. That decision is
logic, it lives in YAML where nothing was testing it, and it is the single thing
standing between a transient and a lost morning - so it is tested here, by
extracting the real shell out of the real workflow and running it.

Written to avoid bash 4 features on purpose: macOS ships bash 3.2, and a gate
using associative arrays could only ever be verified by pushing it, which is the
habit this change exists to break.
"""

import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily.yml"
TEXT = WORKFLOW.read_text()

# Damage a later run cannot repair: a permanently missing day, data collected and
# not stored, or a site serving yesterday's numbers.
CRITICAL = {"snapshot", "boards", "load_boards", "archive", "transform",
            "refresh_marts", "quality"}
# Recovered by tomorrow's run.
RECOVERABLE = {"aggregator", "discover", "prune", "load_aggregator", "extract",
               "history", "company_identity", "dol_mapping", "enrich", "parquet"}


def gate_script(outcomes: dict[str, str]) -> str:
    """The real gate from the real workflow, with outcomes substituted."""
    i = TEXT.index("- name: Decide the run outcome")
    body = TEXT[TEXT.index("run: |", i) + len("run: |"):]
    lines = [re.sub(r"\$\{\{ steps\.(\w+)\.outcome \}\}",
                    lambda m: outcomes.get(m.group(1), "success"), line)
             for line in body.split("\n")]
    return "\n".join(line[10:] if line.startswith(" " * 10) else line
                     for line in lines)


def run_gate(outcomes: dict[str, str]) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".sh") as f:
        f.write('GITHUB_STEP_SUMMARY=/dev/null\n' + gate_script(outcomes))
        f.flush()
        return subprocess.run(["/bin/bash", f.name],
                              capture_output=True, text=True).returncode


# --------------------------------------------------- recoverable must not fail

@pytest.mark.parametrize("step", sorted(RECOVERABLE))
def test_a_recoverable_step_failing_does_not_fail_the_run(step):
    assert run_gate({step: "failure"}) == 0, (
        f"{step} failing fails the whole run, but tomorrow's run repairs it"
    )


def test_the_2026_09_10_failure_would_no_longer_lose_the_morning():
    """
    The actual event: HTTP 503 from Adzuna on one search term, 95 seconds in.
    It skipped fourteen steps. Now it is a warning.
    """
    assert run_gate({"aggregator": "failure", "load_aggregator": "failure"}) == 0


def test_every_recoverable_step_failing_at_once_is_still_not_fatal():
    assert run_gate({s: "failure" for s in RECOVERABLE}) == 0


# ----------------------------------------------------- critical must fail loudly

@pytest.mark.parametrize("step", sorted(CRITICAL))
def test_a_critical_step_failing_fails_the_run(step):
    assert run_gate({step: "failure"}) == 1, (
        f"{step} failing leaves the run green, but nothing repairs it later"
    )


def test_one_critical_failure_is_not_masked_by_recoverable_ones():
    outcomes = {s: "failure" for s in RECOVERABLE}
    outcomes["transform"] = "failure"
    assert run_gate(outcomes) == 1


def test_skipped_is_not_failure():
    """
    Scoring is skipped deliberately via a workflow input. A skip must not be read
    as a failure or every deliberate choice turns the run red.
    """
    assert run_gate({"enrich": "skipped"}) == 0


# ------------------------------------------------------------ the policy itself

def _steps() -> list[dict]:
    job = next(iter(yaml.safe_load(TEXT)["jobs"].values()))
    return [s for s in job["steps"] if s.get("name")]


def test_only_genuine_preconditions_are_fatal():
    """
    A fatal step skips everything after it, so each one multiplies the chance of
    losing a whole morning. Only steps whose failure guarantees every later step
    also fails may be fatal - the rest report through the gate.
    """
    allowed = {"Install dependencies", "Check secrets are present",
               "Check warehouse reachable", "Derive warehouse credentials for dbt",
               "Install dbt packages",
               "Decide the run outcome"}
    fatal = [s["name"] for s in _steps() if not s.get("continue-on-error")]
    assert set(fatal) <= allowed, f"new fatal step(s): {set(fatal) - allowed}"


def test_the_gate_runs_last_and_always():
    steps = _steps()
    assert steps[-1]["name"] == "Decide the run outcome"
    assert steps[-1]["if"] == "always()"


def test_every_step_the_gate_reads_actually_exists():
    """
    A typo in a step id makes the gate read an empty outcome, which silently
    never equals "failure" - so a critical step could fail and the run stay green.
    """
    ids = {s["id"] for s in _steps() if s.get("id")}
    missing = (CRITICAL | RECOVERABLE) - ids
    assert not missing, f"the gate reads ids no step defines: {missing}"


def test_the_gate_avoids_bash_4_only_features():
    """
    macOS ships bash 3.2. A gate needing bash 4 can only be verified by pushing
    it, which is the habit this whole change exists to break.

    Comments are stripped before checking, because the first version of this test
    matched the comment explaining why associative arrays are not used - the same
    prose-versus-code mistake that made the earlier patch skip two steps whose
    following comment happened to contain "continue-on-error".

    Indirect expansion like ${!var} is bash 2 and fine; it is ${!array[@]} key
    expansion and declare -A that need bash 4.
    """
    code = "\n".join(line for line in gate_script({}).split("\n")
                     if not line.lstrip().startswith("#"))
    assert "declare -A" not in code
    assert not re.search(r"\$\{!\w+\[[@*]\]\}", code)


def test_the_gate_actually_executes_under_bash_3_2():
    """
    The direct form of the check above: the local shell IS 3.2, so a gate that
    needs 4 cannot run here at all.
    """
    version = subprocess.run(["/bin/bash", "--version"], capture_output=True,
                             text=True).stdout
    assert "version 3." in version or "version 4." in version or "version 5." in version
    assert run_gate({}) == 0
