-- Snowflake account guardrails and objects for Signal.
--
-- Applied by storage/snowflake_apply.py. Committed rather than typed into a
-- worksheet, for the same reason schema.sql is: an object that exists only
-- because somebody once ran a statement is not reproducible and not reviewable.
--
-- Snowflake's role here is narrow and worth stating. It is not the warehouse
-- behind the site - that is Neon, which is already paid for and answers in
-- milliseconds. It is a second SQL engine that fails differently, so a
-- portability claim can be tested instead of asserted. The one bug class it
-- catches that nothing else does: Postgres regexp_like searches anywhere while
-- Snowflake's is implicitly anchored, and identical SQL classified 1,590
-- postings as internships on Postgres and zero here, compiling cleanly on both.
-- Spark SQL's rlike is unanchored like Postgres, so Databricks cannot catch it.

-- ---------------------------------------------------------------------------
-- 1. The spend cap, first, before anything can run.
--
-- 5 credits a month is about $10 at on-demand pricing on AWS. Measured usage is
-- well under that: an XSMALL warehouse is 1 credit/hour billed per second with a
-- 60-second minimum, and the work here is minutes a week. The cap exists so that
-- a runaway query or a forgotten session cannot produce a surprise bill - this
-- project is funded out of a student's pocket and has already been surprised by
-- one API bill.
--
-- SUSPEND waits for running queries to finish; SUSPEND_IMMEDIATE kills them. Both
-- are set, so the soft stop is tried first.
CREATE RESOURCE MONITOR IF NOT EXISTS SIGNAL_CAP
    WITH CREDIT_QUOTA = 5
    FREQUENCY = MONTHLY
    START_TIMESTAMP = IMMEDIATELY
    TRIGGERS ON 75 PERCENT DO NOTIFY
             ON 100 PERCENT DO SUSPEND
             ON 110 PERCENT DO SUSPEND_IMMEDIATE;

-- Attached to the ACCOUNT, and deliberately NOT to individual warehouses.
--
-- Attaching it to COMPUTE_WH alone left a hole: the account also carries
-- SNOWFLAKE_LEARNING_WH and SYSTEM$STREAMLIT_NOTEBOOK_WH, neither of which had a
-- monitor, and a warehouse created next month would have had none either.
--
-- Snowflake refuses to attach one monitor at both levels - "Unable to attach
-- resource monitor to account because it is currently set for one or more
-- warehouses" - and a warehouse-level monitor overrides the account one. So the
-- warehouse assignment is cleared and the cap lives at the account, where it
-- governs every warehouse that does not have its own. One cap, nothing outside
-- it, and 5 credits total rather than 5 per warehouse.
ALTER WAREHOUSE COMPUTE_WH UNSET RESOURCE_MONITOR;
ALTER ACCOUNT SET RESOURCE_MONITOR = SIGNAL_CAP;

-- ---------------------------------------------------------------------------
-- 2. The warehouse, sized down and quick to sleep.
--
-- XSMALL because the largest table here is 108,001 rows. AUTO_SUSPEND = 60 so an
-- idle session cannot bill indefinitely: the expensive mistake available on
-- Snowflake is not query cost but leaving a warehouse awake, which is why the
-- loader does all of its Postgres reading before it opens a connection.
CREATE WAREHOUSE IF NOT EXISTS COMPUTE_WH
    WITH WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE;

ALTER WAREHOUSE COMPUTE_WH SET
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    STATEMENT_TIMEOUT_IN_SECONDS = 900;

-- ---------------------------------------------------------------------------
-- 3. A least-privilege role, so the pipeline stops running as ACCOUNTADMIN.
--
-- ACCOUNTADMIN can drop the account's billing configuration. A loader needs to
-- write tables in one database and use one warehouse, and nothing else. The
-- difference matters most when the credential is the one thing most likely to
-- leak, which is exactly what a CI secret is.
CREATE DATABASE IF NOT EXISTS SIGNAL_DB;
CREATE ROLE IF NOT EXISTS SIGNAL_LOADER;

GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE SIGNAL_LOADER;
GRANT USAGE ON DATABASE SIGNAL_DB TO ROLE SIGNAL_LOADER;
GRANT USAGE, CREATE SCHEMA ON DATABASE SIGNAL_DB TO ROLE SIGNAL_LOADER;
GRANT ALL ON SCHEMA SIGNAL_DB.PUBLIC TO ROLE SIGNAL_LOADER;
GRANT ALL ON FUTURE TABLES IN SCHEMA SIGNAL_DB.PUBLIC TO ROLE SIGNAL_LOADER;
GRANT ALL ON FUTURE VIEWS IN SCHEMA SIGNAL_DB.PUBLIC TO ROLE SIGNAL_LOADER;
GRANT ALL ON ALL TABLES IN SCHEMA SIGNAL_DB.PUBLIC TO ROLE SIGNAL_LOADER;
GRANT ALL ON ALL VIEWS IN SCHEMA SIGNAL_DB.PUBLIC TO ROLE SIGNAL_LOADER;

-- The human account gets the role so the same key can use it.
GRANT ROLE SIGNAL_LOADER TO USER ZOHAIB365;
