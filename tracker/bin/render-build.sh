#!/usr/bin/env bash
# Render's build command for the tracker app.
#
# Migrations run HERE, not in a pre-deploy hook, because pre-deploy is a paid
# feature and this service is on the free plan. That has one consequence worth
# knowing: the build runs before the new version is live, so a migration that
# fails takes the deploy down with it rather than leaving a half-migrated app
# serving traffic. For a single-user tool that is the right trade.
#
# Migrations also go through Neon's DIRECT endpoint rather than the pooler.
# Rails takes a Postgres advisory lock around a migration, and Neon's default
# endpoint is PgBouncer in transaction mode, which cannot hold one. The app's
# own DATABASE_URL is the direct endpoint too - see config/database.yml for the
# measurement that forced that - so MIGRATE_DATABASE_URL is belt and braces for
# the case where someone later points DATABASE_URL at the pooler.
set -o errexit
set -o pipefail
set -o nounset

echo "--> bundle"
bundle install

echo "--> assets"
bundle exec rails tailwindcss:build
bundle exec rails assets:precompile
bundle exec rails assets:clean

echo "--> migrate"
if [ -n "${MIGRATE_DATABASE_URL:-}" ]; then
  DATABASE_URL="$MIGRATE_DATABASE_URL" bundle exec rails db:migrate
else
  bundle exec rails db:migrate
fi

# The single user. Idempotent: re-running updates the password rather than
# failing on the unique index, so rotating it is a redeploy.
echo "--> seed"
bundle exec rails db:seed
