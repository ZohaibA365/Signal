-- The schema Rails owns, and the role that confines it there.
--
-- Run ONCE, by hand, with the warehouse OWNER credentials:
--
--     psql "$NEON_DIRECT_URL" -v rails_password="'<a strong password>'" \
--          -f tracker/script/setup_database_role.sql
--
-- This is the Rails counterpart of service/sandbox.py, and deliberately the same
-- shape: a schema the application owns, a role that can write only inside it, and
-- SELECT granted table-by-table rather than schema-wide. That file explains why
-- enumeration matters: "a future table holding something private should not become
-- readable by the public service just because it was created." The same applies
-- here.
--
-- Everything below is idempotent and additive. Nothing drops, nothing revokes a
-- privilege the pipeline needs, and re-running it is safe.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 1. The schema Rails owns.
--
-- Named `tracker`, and no table inside it may reuse a name from `public`.
-- search_path resolution works in both directions: a `tracker.outreach_tracker`
-- would silently shadow the real one for any connection with tracker first on its
-- path. The Rails tables are `applications`, `application_notes` and
-- `outreach_approvals` precisely so that cannot happen.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS tracker;

-- ---------------------------------------------------------------------------
-- 2. The role.
-- ---------------------------------------------------------------------------
-- Create it only if absent, then set the password either way, so re-running this
-- rotates the password rather than failing.
--
-- Written with \gexec rather than a DO block on purpose: psql does NOT interpolate
-- :variables inside a dollar-quoted body, so the password would have arrived
-- literally as the text ":rails_password".
SELECT 'CREATE ROLE signal_rails LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_rails')
\gexec

ALTER ROLE signal_rails LOGIN PASSWORD :rails_password;

-- Rails needs CREATE on `tracker` so migrations can build tables there. The tables
-- it creates will be owned by signal_rails.
--
-- The schema itself stays owned by the warehouse owner. Transferring it would need
-- `ALTER SCHEMA tracker OWNER TO signal_rails`, which Neon refuses - "must be able
-- to SET ROLE signal_rails" - because the Neon owner role is not a superuser and is
-- not a member of signal_rails. CREATE is the privilege that actually matters, so
-- nothing is lost.
GRANT USAGE, CREATE ON SCHEMA tracker TO signal_rails;

-- ---------------------------------------------------------------------------
-- 3. Reading `public` - enumerated, never schema-wide.
--
-- Two tables, because two are what the app reads:
--   apply_queue       the posting list, with eligibility and fit scores (dbt)
--   outreach_tracker  the agent's drafts (written by agent/tools.py)
--
-- Adding a third is a one-line change here plus a line in
-- script/verify_isolation.rb. That friction is the point.
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO signal_rails;
GRANT SELECT ON public.apply_queue      TO signal_rails;
GRANT SELECT ON public.outreach_tracker TO signal_rails;

-- ---------------------------------------------------------------------------
-- 4. No writing to public. Ever.
-- ---------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM signal_rails;
REVOKE CREATE ON SCHEMA public FROM signal_rails;

-- ---------------------------------------------------------------------------
-- 5. Survive dbt's nightly rebuild.
--
-- `dbt build` DROPS and recreates every mart, and a grant belongs to the table
-- rather than to the name - so the grant on apply_queue above vanishes with it.
-- This repo has already been bitten by exactly that: service/sandbox.py:110-121
-- records the public page working all day and then answering "permission denied
-- for table dim_company" the moment the pipeline rebuilt it.
--
-- ALTER DEFAULT PRIVILEGES fixes it for tables created in future. It is broader
-- than the enumeration above - any new public table becomes readable to Rails -
-- and that is a real trade, accepted here for the same reason sandbox.py accepts
-- it: without it the app breaks every morning at 07:00.
--
-- Default privileges attach to the role that CREATES the object. With no FOR ROLE
-- clause this applies to the role running the script - which must therefore be the
-- same role dbt connects as. It is: both are the warehouse owner.
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO signal_rails;

-- ---------------------------------------------------------------------------
-- 6. Report what now holds, so the operator can see it rather than trust it.
-- ---------------------------------------------------------------------------
\echo ''
\echo '--- signal_rails privileges on public (expect SELECT only) ---'
SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.table_privileges
WHERE grantee = 'signal_rails' AND table_schema = 'public'
GROUP BY table_name
ORDER BY table_name;

\echo ''
\echo '--- schemas (tracker exists; owner stays neondb_owner, see section 2) ---'
SELECT nspname AS schema, pg_get_userbyid(nspowner) AS owner
FROM pg_namespace
WHERE nspname IN ('public', 'tracker', 'demo')
ORDER BY nspname;

\echo ''
\echo 'Done. Now run: cd tracker && bin/rails runner script/verify_isolation.rb'
\echo 'That PROVES the confinement by attempting forbidden writes.'
