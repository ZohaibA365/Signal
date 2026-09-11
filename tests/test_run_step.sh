#!/usr/bin/env bash
# Behaviour tests for scripts/run_step.sh, the wrapper every pipeline step runs
# through. Plain bash rather than pytest, because what is being tested IS bash -
# retry counting, exit-status propagation and annotation shape - and testing it
# through a Python subprocess layer would only add a place for the test to be
# wrong about the thing it is checking.
#
# Run: bash tests/test_run_step.sh
set -u
cd "$(dirname "$0")/.."

pass=0; fail=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# A command that fails its first N invocations, then succeeds.
cat > "$tmp/flaky" <<'INNER'
#!/usr/bin/env bash
counter="$1"; fail_times="$2"
n=$(cat "$counter" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$counter"
if [ "$n" -le "$fail_times" ]; then echo "transient failure $n" >&2; exit 1; fi
echo "ok on attempt $n"
INNER
chmod +x "$tmp/flaky"

check() {  # check <label> <expected-exit> <expected-attempts> <retries> <fail-times>
    local label="$1" want_exit="$2" want_tries="$3" retries="$4" fails="$5"
    local counter="$tmp/c.$RANDOM"
    RETRIES="$retries" RETRY_WAIT_SECONDS=0 GITHUB_STEP_SUMMARY=/dev/null \
        bash scripts/run_step.sh "$label" "$tmp/flaky" "$counter" "$fails" >/dev/null 2>&1
    local got_exit=$? got_tries
    got_tries=$(cat "$counter" 2>/dev/null || echo 0)
    if [ "$got_exit" = "$want_exit" ] && [ "$got_tries" = "$want_tries" ]; then
        printf '  ok    %-52s exit=%s attempts=%s\n' "$label" "$got_exit" "$got_tries"
        pass=$((pass + 1))
    else
        printf '  FAIL  %-52s exit=%s/%s attempts=%s/%s\n' \
               "$label" "$got_exit" "$want_exit" "$got_tries" "$want_tries"
        fail=$((fail + 1))
    fi
}

# A step that works costs exactly one attempt - retries must never add latency to
# the normal case, which is every step on a good morning.
check "success costs one attempt"                0 1 2 0
check "default two attempts, fails once"         0 2 2 1
check "default two attempts, always fails"       1 2 2 99
check "three attempts, fails twice"              0 3 3 2
check "retries disabled, no second attempt"      1 1 1 99
check "four attempts for a cold warehouse"       0 4 4 3

# The failure annotation is the only channel that comes back out of the public
# API, so a failing step must still emit one after exhausting its retries.
out="$(RETRIES=2 RETRY_WAIT_SECONDS=0 GITHUB_STEP_SUMMARY=/dev/null \
       bash scripts/run_step.sh "Annotated" "$tmp/flaky" "$tmp/ann" 99 2>&1 || true)"
if printf '%s' "$out" | grep -q '^::error title=Annotated failed::'; then
    printf '  ok    %-52s\n' "emits an error annotation after giving up"; pass=$((pass + 1))
else
    printf '  FAIL  %-52s\n' "no error annotation after giving up"; fail=$((fail + 1))
fi
if printf '%s' "$out" | grep -q '::warning::Annotated failed (attempt 1 of 2)'; then
    printf '  ok    %-52s\n' "warns between attempts"; pass=$((pass + 1))
else
    printf '  FAIL  %-52s\n' "no warning between attempts"; fail=$((fail + 1))
fi

# Annotations cannot contain raw newlines; %0A is the documented escape.
if [ "$(printf '%s' "$out" | grep -c '^::error')" = "1" ]; then
    printf '  ok    %-52s\n' "the annotation is a single line"; pass=$((pass + 1))
else
    printf '  FAIL  %-52s\n' "annotation spans multiple lines"; fail=$((fail + 1))
fi

echo
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
