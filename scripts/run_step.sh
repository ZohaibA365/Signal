#!/usr/bin/env bash
#
# Run a pipeline step and make its failure readable.
#
# GitHub publishes three things about a workflow run: step names, step logs and
# annotations. Step names say nothing, step logs need repo-owner auth, and
# annotations are the only channel that comes back out of the public API. So a
# step that dies without emitting an annotation says exactly one thing to
# anyone diagnosing it later:
#
#     Process completed with exit code 1
#
# That is all that escaped the scheduled run on 2026-09-10, which failed at the
# aggregator ingest and skipped the fourteen steps after it. It is also why
# three separate diagnoses of the dbt Transform step were guesses during the
# stretch where this pipeline failed nineteen mornings running. Transform was
# given a bespoke version of this wrapper and stopped being a mystery; every
# other step was still flying blind.
#
# Usage:
#     scripts/run_step.sh "Step title" command arg arg ...
#
# Exits with the wrapped command's status, so continue-on-error and hard steps
# both behave exactly as they did before.

set -uo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <title> <command> [args...]" >&2
    exit 2
fi

title="$1"
shift

# A plain path, not mktemp with a template. GNU coreutils mktemp requires the
# X's to be the last characters of the template and rejects "step.XXXXXX.log",
# while BSD mktemp on macOS accepts it - so a template that tests clean locally
# would have failed every wrapped step on an Ubuntu runner.
log="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/signal-step-$$.log"
: > "$log"

set -o pipefail
"$@" 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"

if [ "$status" -eq 0 ]; then
    rm -f "$log"
    exit 0
fi

# The job summary gets room to breathe: it renders markdown on the run page and
# has no length limit worth worrying about at forty lines.
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo ""
        echo "### ${title} failed (exit ${status})"
        echo '```'
        tail -40 "$log"
        echo '```'
    } >> "$GITHUB_STEP_SUMMARY"
fi

# The annotation has to survive being a single shell argument on one line:
#   - raw newlines are not permitted, %0A is the documented escape
#   - a double quote would terminate the title= attribute early
# Pulling the LAST matching lines rather than the first is deliberate; a Python
# traceback puts the actual exception at the bottom.
detail="$(grep -iE 'error|traceback|exception|http [0-9]{3}|failed|refused|timeout|quota|limit|denied|not found|no such' "$log" \
          | tail -10 \
          | sed 's/"/'"'"'/g' \
          | awk '{printf "%s%%0A", $0}')"

if [ -z "$detail" ]; then
    detail="$(tail -3 "$log" | sed 's/"/'"'"'/g' | awk '{printf "%s%%0A", $0}')"
fi
if [ -z "$detail" ]; then
    detail="no output captured; exit status ${status}"
fi

echo "::error title=${title} failed::${detail}"
rm -f "$log"
exit "$status"
