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
# It also retries, because most of what broke this pipeline was transient. Set
# RETRIES to the number of attempts; the default of 1 means no retry, so a step
# only retries where that is actually safe. Waits 15s then 45s between attempts,
# which is long enough to outlast the kind of outage that was killing runs: the
# aggregator returned HTTP 503 for about 14 seconds on 2026-09-10 and the whole
# morning was lost to it.
#
# Retry only idempotent steps. Every one that does retry either resumes from what
# it already wrote, upserts on a natural key, or recomputes from scratch.
#
# Usage:
#     scripts/run_step.sh "Step title" command arg arg ...
#     RETRIES=3 scripts/run_step.sh "Step title" command ...
#     RETRY_WAIT_SECONDS=0 RETRIES=3 scripts/run_step.sh ...   # in tests
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

retries="${RETRIES:-1}"
attempt=1
status=0
set -o pipefail
while : ; do
    : > "$log"
    "$@" 2>&1 | tee "$log"
    status="${PIPESTATUS[0]}"
    [ "$status" -eq 0 ] && break
    [ "$attempt" -ge "$retries" ] && break
    # Overridable so the tests need not sleep through it. A backoff that can
    # only be exercised in real time is a backoff nothing tests.
    wait="${RETRY_WAIT_SECONDS:-$((attempt * 30 + 15))}"
    echo "::warning::${title} failed (attempt ${attempt} of ${retries}), retrying in ${wait}s"
    attempt=$((attempt + 1))
    sleep "$wait"
done

if [ "$status" -eq 0 ]; then
    if [ "$attempt" -gt 1 ]; then
        echo "::warning::${title} succeeded on attempt ${attempt} of ${retries}"
    fi
    rm -f "$log"
    exit 0
fi

# The job summary gets room to breathe: it renders markdown on the run page and
# has no length limit worth worrying about at forty lines.
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo ""
        echo "### ${title} failed (exit ${status}) after ${attempt} attempt(s)"
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
