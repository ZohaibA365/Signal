"""
Score the enrichment layer against a hand-labelled golden set.

Run this after changing the enrichment prompt or switching models, and compare
against a previous run. It exists because all three of the model's judgements
reach the published board, the prompt gets edited, and until now nothing measured
whether an edit made things better or worse.

It calls ai_layer.enrich.assess() directly. It does not reimplement the prompt,
the schema, or the API call - a harness that scored its own copy of the prompt
would keep passing while the real one broke.

    python eval/run_eval.py --dry-run          # no API call, no cost
    python eval/run_eval.py                    # a real run, on Haiku
    python eval/run_eval.py --model claude-opus-5
    python eval/run_eval.py --compare-to eval/results/eval_20260913T194500.json

Cost. One API call per example, so five examples on Haiku is a fraction of a
cent. --dry-run makes none at all and is the right way to test changes to this
file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ai_layer is imported as a directory of modules rather than a package - enrich.py
# itself does `from candidate_profile import ...` - so both it and this directory
# go on the path.
sys.path.insert(0, str(ROOT / "ai_layer"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_utils import (  # noqa: E402
    CATEGORICAL_FIELDS,
    REGRESSION,
    compare,
    grade_example,
    row_from_example,
    summarise,
    validate_example,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("eval")

GOLDEN_SET = ROOT / "eval" / "golden_set.json"
RESULTS_DIR = ROOT / "eval" / "results"

# Haiku by default, because a run costs real money and most runs are checking that
# a prompt edit did not break something obvious.
#
# Know what that does and does not prove. The daily pipeline scores intern and
# entry roles - the ones actually applied to - with Opus, and everything else with
# Haiku. So a green Haiku eval says the bulk path is healthy and says nothing
# about the path the important roles take. The run warns about this every time
# rather than letting it become an assumption; --model claude-opus-5 tests it.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
PRODUCTION_MODEL_FOR_KEY_ROLES = "claude-opus-5"

# The prompt is built from the candidate profile at import time, so the profile is
# part of what is being evaluated - a different profile is a different prompt and
# the scores are not comparable across them.
#
# It has to be pinned here because ai_layer/candidate_profile.py defaults to
# 'cobol' while the daily pipeline sets SIGNAL_PROFILE=student in its environment.
# The first real run of this harness scored every posting against a mainframe
# programmer and explained itself with "no mainframe or COBOL involvement", which
# is how this was found. An eval that silently measures a different candidate than
# production is worse than no eval.
DEFAULT_PROFILE = "student"

# One timestamp per process, so every artifact of a run shares it. Same format as
# storage/archive_postings.py, for one convention rather than two.
RUN_STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def colour(text: str, code: str) -> str:
    """ANSI only when a person is watching; redirected output stays plain."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def load_golden_set(path: Path) -> list[dict]:
    """Read and validate the golden set, refusing to spend money on a broken one."""
    with open(path, encoding="utf-8") as fh:
        examples = json.load(fh)

    problems = {}
    for i, example in enumerate(examples):
        found = validate_example(example)
        if found:
            problems[example.get("job_id", f"entry {i}")] = found
    if problems:
        for job_id, found in problems.items():
            log.error("  %s: %s", job_id, "; ".join(found))
        raise SystemExit(f"{len(problems)} golden-set entr(y/ies) are malformed")

    seen = [e["job_id"] for e in examples]
    if len(set(seen)) != len(seen):
        raise SystemExit("golden set has duplicate job_ids, so results cannot be paired")
    return examples


def git_commit() -> str:
    """Which commit produced this result, so an old file stays interpretable."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def assess_all(examples: list[dict], model: str) -> tuple[list[dict], float]:
    """
    Score every example with the real enrichment function. Returns actuals + cost.

    Imported here rather than at module scope so --dry-run needs neither the
    anthropic package nor an API key.
    """
    import anthropic
    from enrich import RATES, assess

    client = anthropic.Anthropic()
    rate_in, rate_cached, rate_out = RATES[model]
    actuals, cost = [], 0.0

    for example in examples:
        assessment, usage = assess(client, row_from_example(example), model=model)
        # Sequential, not threaded. Five to fifty examples is not worth the
        # concurrency, and a failure here should stop rather than race.
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        fresh = (usage.input_tokens or 0) - cached
        cost += fresh * rate_in + cached * rate_cached + (usage.output_tokens or 0) * rate_out
        actuals.append({
            "sponsorship_signal": assessment.sponsorship_signal,
            "eligibility": assessment.eligibility,
            "fit_score": assessment.fit_score,
            "eligibility_reason": assessment.eligibility_reason,
            "visa_reasoning": assessment.visa_reasoning,
            "fit_reasoning": assessment.fit_reasoning,
        })
        log.info("  scored %s", example["job_id"])
    return actuals, cost


def expected_as_actual(example: dict) -> dict:
    """
    A perfect answer, for --dry-run.

    Echoing the expected values back exercises loading, adapting, grading,
    formatting and writing - everything except the API call - so a change to this
    harness can be tested for nothing. A dry run is always 100%, which is the
    point: any other number means the harness itself is broken.
    """
    return {
        "sponsorship_signal": example["expected_sponsorship_signal"],
        "eligibility": example["expected_eligibility"],
        "fit_score": example["expected_fit_score"],
        "eligibility_reason": "(dry run)",
        "visa_reasoning": "(dry run)",
        "fit_reasoning": "(dry run)",
    }


def prompt_hash() -> str:
    """
    A fingerprint of the system prompt that produced a result.

    Recorded in every results file so that diffing two runs can distinguish a
    prompt edit from the model simply answering differently twice - without it, a
    comparison cannot tell improvement from noise. Read lazily and forgiving of
    failure, so a dry run still works if the enrichment module cannot be imported.
    """
    try:
        from enrich import SYSTEM_PROMPT
    except Exception:                                  # noqa: BLE001
        return "unavailable"
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def print_table(per_example: list[dict]) -> None:
    """One row per example. f-strings, matching quality/expectations.py."""
    print()
    print(f"  {'posting':<34} {'sponsorship':<24} {'eligibility':<22} {'fit':<14}")
    print(f"  {'-' * 34} {'-' * 24} {'-' * 22} {'-' * 14}")
    for r in per_example:
        cells = []
        for field in CATEGORICAL_FIELDS:
            f = r[field]
            mark = "ok" if f["pass"] else "MISS"
            cells.append(f"{f['actual']} [{mark}]" if f["pass"]
                         else f"{f['actual']} != {f['expected']}")
        fit = r["fit_score"]
        fit_cell = (f"{fit['actual']} [ok]" if fit["pass"]
                    else f"{fit['actual']} != {fit['expected']}")
        label = f"{(r['company'] or '?')[:14]} {(r['title'] or '')[:18]}"
        line = f"  {label:<34} {cells[0]:<24} {cells[1]:<22} {fit_cell:<14}"
        print(line if r["passed_all"] else colour(line, "33"))


def print_summary(summary: dict, model: str, cost: float, dry: bool) -> None:
    print()
    print(f"  examples                       {summary['examples']}")
    for field in CATEGORICAL_FIELDS:
        print(f"  {field + ' accuracy':<30} "
              f"{summary[f'{field}_accuracy_pct']}%  "
              f"({summary[f'{field}_correct']}/{summary['examples']})")
    print(f"  {'fit_score pass rate':<30} {summary['fit_score_pass_rate_pct']}%  "
          f"({summary['fit_score_passed']}/{summary['examples']}, "
          f"within {summary['fit_score_tolerance']} points)")
    print(f"  {'fit_score mean abs error':<30} {summary['fit_score_mean_absolute_error']}")
    failed = summary["failed_job_ids"]
    print(f"  {'examples failing any field':<30} {len(failed)}")
    for job_id in failed:
        print(f"      {job_id}")
    print()
    print(f"  model: {model}   profile: {os.environ.get('SIGNAL_PROFILE', '?')}"
          + ("   (dry run, no API call)" if dry else f"   cost: ${cost:.4f}"))
    if not dry and model != PRODUCTION_MODEL_FOR_KEY_ROLES:
        print(colour(
            f"  NOTE: the daily pipeline scores intern and entry roles with "
            f"{PRODUCTION_MODEL_FOR_KEY_ROLES}, so this run does not cover them. "
            f"Use --model {PRODUCTION_MODEL_FOR_KEY_ROLES} to test that path.", "33"))


def print_comparison(diff: dict, previous: Path) -> None:
    print()
    print(f"  compared against {previous.name}: {diff['compared']} example(s) in both")
    if not diff["changes"]:
        print("  nothing changed.")
        return
    for c in diff["changes"]:
        line = (f"      {c['status'].upper():<12} {c['job_id'][:40]:42} {c['field']:<20} "
                f"{c['was']} -> {c['now']}  (expected {c['expected']})")
        print(colour(line, "31") if c["status"] == REGRESSION else line)
    n = len(diff["regressions"])
    if n:
        print(colour(f"  {n} REGRESSION(S): a field that passed before fails now.", "31"))
    else:
        print(f"  no regressions. {len(diff['improvements'])} improvement(s).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Score enrichment against the golden set")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"default {DEFAULT_MODEL}")
    ap.add_argument("--golden-set", type=Path, default=GOLDEN_SET)
    ap.add_argument("--limit", type=int, help="score only the first N examples")
    ap.add_argument("--dry-run", action="store_true",
                    help="exercise the harness with no API call and no cost")
    ap.add_argument("--compare-to", type=Path, metavar="RESULTS.json",
                    help="diff against a previous run and flag regressions")
    ap.add_argument("--out", type=Path, help="where to write results")
    ap.add_argument("--fail-under", type=float, metavar="PCT",
                    help="exit non-zero if any accuracy or pass rate is below this")
    ap.add_argument("--profile", default=DEFAULT_PROFILE,
                    help=f"candidate profile the prompt is built from "
                         f"(default {DEFAULT_PROFILE}, matching the daily pipeline)")
    args = ap.parse_args()

    # Before any import of the enrichment module: candidate_profile reads this at
    # import time and the system prompt is built from it.
    os.environ["SIGNAL_PROFILE"] = args.profile

    examples = load_golden_set(args.golden_set)
    if args.limit:
        examples = examples[:args.limit]
    log.info("%s example(s) from %s", len(examples), args.golden_set.name)

    if args.dry_run:
        actuals, cost = [expected_as_actual(e) for e in examples], 0.0
    else:
        actuals, cost = assess_all(examples, args.model)

    per_example = [grade_example(e, a) for e, a in zip(examples, actuals, strict=True)]
    for record, actual in zip(per_example, actuals, strict=True):
        # The model's own reasoning, kept so a failure can be read rather than
        # guessed at. This is usually the first thing worth looking at.
        record["reasoning"] = {k: actual[k] for k in
                               ("eligibility_reason", "visa_reasoning", "fit_reasoning")}
    summary = summarise(per_example)

    print_table(per_example)
    print_summary(summary, args.model, cost, args.dry_run)

    result = {
        "run": RUN_STAMP,
        "finished_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "profile": args.profile,
        "dry_run": args.dry_run,
        "cost_usd": round(cost, 6),
        "git_commit": git_commit(),
        # Which prompt produced this. Without it, diffing two runs cannot tell a
        # prompt change from the model simply answering differently twice.
        "prompt_sha256": prompt_hash(),
        "golden_set": str(args.golden_set.relative_to(ROOT)),
        "summary": summary,
        "examples": per_example,
    }

    diff = None
    if args.compare_to:
        with open(args.compare_to, encoding="utf-8") as fh:
            previous = json.load(fh)
        diff = compare(per_example, previous["examples"])
        print_comparison(diff, args.compare_to)
        result["compared_to"] = str(args.compare_to)
        result["comparison"] = diff
        if previous.get("prompt_sha256") == result["prompt_sha256"]:
            print("  (the prompt is unchanged between these runs, so any difference "
                  "is the model answering differently, not an edit.)")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or RESULTS_DIR / f"eval_{RUN_STAMP}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\n  wrote {out_path.relative_to(ROOT)}")

    # Exit codes, so this can become an Airflow task or a CI gate without a
    # rewrite. Off unless asked for: a run is a measurement until you decide it
    # is a gate.
    if diff and diff["regressions"]:
        raise SystemExit(f"{len(diff['regressions'])} regression(s) against {args.compare_to}")
    if args.fail_under is not None:
        rates = [summary[f"{f}_accuracy_pct"] for f in CATEGORICAL_FIELDS]
        rates.append(summary["fit_score_pass_rate_pct"])
        if min(rates) < args.fail_under:
            raise SystemExit(f"lowest rate {min(rates)}% is below --fail-under "
                             f"{args.fail_under}%")


if __name__ == "__main__":
    main()
