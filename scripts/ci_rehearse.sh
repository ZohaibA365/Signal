#!/usr/bin/env bash
#
# Run what CI runs, locally, against a throwaway Postgres container.
#
# This exists because of how the CI failures actually went: a fixture column
# list, a schema.sql omission and a regex that behaves differently per engine
# were each discovered only after a push, because there was no Postgres on this
# laptop and the hosted warehouse could not be used - it holds the live site's
# data, and the fixture would overwrite it. So the only way to test a change to
# the build was to push it and wait.
#
# A container costs about ninety seconds and removes that entirely. It mirrors
# .github/workflows/ci.yml step for step, on port 5455 so it cannot collide with
# anything already listening on 5432 or 5433.
#
# The same argument applies to the Python environment, and that gap cost a run:
# the laptop's .venv has the Snowflake extras installed, so a test importing
# cryptography passed here and failed collection in CI, which exits 2 and reports
# nothing about the code. CLEAN=1 builds an environment from requirements-dev.txt
# alone - exactly what CI installs - and runs the tests in it. It is cached
# between runs, so only the first one pays for the install.
#
# Usage:
#   bash scripts/ci_rehearse.sh          # rehearse, then remove the container
#   CLEAN=1 bash scripts/ci_rehearse.sh  # also test in a CI-equivalent venv
#   KEEP=1 bash scripts/ci_rehearse.sh   # leave it running to poke at by hand
set -uo pipefail

cd "$(dirname "$0")/.."

NAME=signal-ci-rehearse
PORT=5455
DBURL="postgresql://signal:signal@localhost:5432/signal"   # as seen inside the container
PY=.venv/bin/python
DBT=.venv/bin/dbt

fail() { echo "FAIL: $1"; exit 1; }

docker info >/dev/null 2>&1 || fail "Docker is not running - start Docker Desktop first"

docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" \
    -e POSTGRES_DB=signal -e POSTGRES_USER=signal -e POSTGRES_PASSWORD=signal \
    -p "$PORT":5432 postgres:16 >/dev/null || fail "container did not start"

cleanup() { [ -n "${KEEP:-}" ] || docker rm -f "$NAME" >/dev/null 2>&1; }
trap cleanup EXIT

for _ in $(seq 1 60); do
    docker exec "$NAME" pg_isready -U signal >/dev/null 2>&1 && break
    sleep 2
done
docker exec "$NAME" pg_isready -U signal >/dev/null 2>&1 || fail "postgres never became ready"
echo "==> postgres up on localhost:$PORT"

# psql runs inside the container, so no local postgresql-client is needed.
psql_f() { docker exec -i "$NAME" psql "$DBURL" -v ON_ERROR_STOP=1 -f - ; }

"$PY" -m pytest -q || fail "unit tests"
bash tests/test_run_step.sh >/dev/null || fail "step wrapper tests"
echo "==> tests pass"

if [ -n "${CLEAN:-}" ]; then
    CIVENV="${CIVENV:-/tmp/signal-civenv}"
    if [ ! -x "$CIVENV/bin/pytest" ]; then
        echo "==> building a CI-equivalent environment in $CIVENV (first run only)"
        python3 -m venv "$CIVENV" || fail "could not create $CIVENV"
        "$CIVENV/bin/pip" install -q --upgrade pip
        # dbt-core fetches a wheel from GitHub while building its metadata, and a
        # bare venv has no certificate bundle to verify it with. The one in .venv
        # is as good as any.
        CERT=$("$PY" -c "import certifi; print(certifi.where())" 2>/dev/null || true)
        SSL_CERT_FILE="$CERT" REQUESTS_CA_BUNDLE="$CERT" \
            "$CIVENV/bin/pip" install -q -r requirements-dev.txt ruff \
            || fail "could not install requirements-dev.txt - see above"
    fi
    "$CIVENV/bin/python" -m pytest -q || fail "tests in a CI-equivalent environment"
    "$CIVENV/bin/ruff" check ingestion storage ai_layer outreach streaming quality \
        processing analytics tests scripts || fail "lint in a CI-equivalent environment"
    echo "==> tests and lint pass with only what CI installs"
fi

psql_f < storage/schema.sql >/dev/null || fail "storage/schema.sql"
echo "==> schema applied"

psql_f < tests/fixtures/seed_ci.sql >/dev/null || fail "tests/fixtures/seed_ci.sql"
echo "==> fixture seeded"

# dbt needs its own working directory, and dbt_packages/ is gitignored, so the
# macros the models use are not in a fresh checkout either.
log=/tmp/signal-rehearse-dbt.log
(
    cd dbt_signal || exit 1
    export POSTGRES_HOST=localhost POSTGRES_PORT="$PORT" POSTGRES_DB=signal
    export POSTGRES_USER=signal POSTGRES_PASSWORD=signal DO_NOT_TRACK=1
    [ -d dbt_packages ] || "../$DBT" deps || exit 1
    "../$DBT" build --profiles-dir . --no-version-check
) > "$log" 2>&1 || { tail -40 "$log"; fail "dbt build - full log in $log"; }
# dbt colours its output, so the counts line carries escape codes.
grep -a "Done\." "$log" | tail -1 | sed $'s/\033\[[0-9;]*m//g'
echo "==> dbt build passed"

psql_f < tests/fixtures/assert_ci_behaviour.sql || fail "behavioural assertions"
echo "==> REHEARSAL PASSED"
