#!/usr/bin/env bash
#
# Give dbt the warehouse credentials it needs, derived from DATABASE_URL.
#
# dbt_signal/profiles.yml reads NEON_USER and NEON_PASSWORD with no defaults, on
# purpose: the file is committed, so a default would put a password in git
# history. Locally those come from .env, which dbt loads by itself - its CLI
# calls load_dotenv on startup. A runner has no .env, because .env is gitignored,
# so dbt failed at parse time with "Env var required but not provided:
# 'NEON_USER'" and exited 2 instantly.
#
# That auto-loading is why the failure resisted diagnosis: the same command
# passes locally and fails in the runner, and the error never leaves it.
#
# Both values are derived from DATABASE_URL rather than added as their own
# secrets, so there is one credential to rotate rather than three that drift.
#
# This lives in a script rather than inline in a workflow because two workflows
# need it - the daily pipeline and the parity check - and the second one failing
# for want of a copy of the first one's setup is the kind of gap that costs a day
# to find.
#
# Usage, from a workflow step:
#     bash scripts/derive_dbt_credentials.sh
set -uo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
    echo "::error::DATABASE_URL is not set, so dbt has no warehouse credentials"
    exit 1
fi

CREDS=$(python - <<'PY'
import os
import urllib.parse as up

raw = os.environ["DATABASE_URL"].strip().strip("'\"")
p = up.urlparse(raw)
user = up.unquote(p.username or "")
pw = up.unquote(p.password or "")
if not user or not pw:
    raise SystemExit("DATABASE_URL carries no user or password")
print(user)
print(pw)
PY
) || { echo "::error::could not read credentials out of DATABASE_URL"; exit 1; }

NEON_USER=$(printf '%s\n' "$CREDS" | sed -n 1p)
NEON_PASSWORD=$(printf '%s\n' "$CREDS" | sed -n 2p)

# Two destinations, and they are not interchangeable: ::add-mask:: is a workflow
# command and must go to stdout, while NAME=VALUE pairs go to the $GITHUB_ENV
# file. Writing the mask command into the env file fails the step with "Invalid
# format".
echo "::add-mask::$NEON_PASSWORD"

if [ -n "${GITHUB_ENV:-}" ]; then
    echo "NEON_USER=$NEON_USER" >> "$GITHUB_ENV"
    echo "NEON_PASSWORD=$NEON_PASSWORD" >> "$GITHUB_ENV"
else
    # Outside a runner there is no $GITHUB_ENV to write to, so say what would
    # have been exported. That makes this runnable by hand when diagnosing.
    echo "would export NEON_USER and NEON_PASSWORD (no GITHUB_ENV set)"
fi

echo "derived credentials for user $NEON_USER"
