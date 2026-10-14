-- Stand-ins for the two warehouse relations the Rails app reads.
--
-- These exist because a fresh test database has neither:
--
--   public.apply_queue       is a dbt model (materialized='table'). Building it for
--                            real would mean running dbt, which needs the whole
--                            Python project, a seeded corpus and minutes of work.
--   public.outreach_tracker  is declared in storage/schema.sql and written by
--                            agent/tools.py.
--
-- They are stand-ins, NOT the real definitions, so they carry a drift risk: a
-- column added to the real apply_queue and used by Rails would pass here and fail
-- in production. spec/models/warehouse_contract_spec.rb guards that by asserting
-- every column the Rails code actually references exists below, and by comparing
-- the outreach_tracker columns against storage/schema.sql.
--
-- Only the columns the app reads are included. A faithful copy of all 35
-- apply_queue columns would be a second place to maintain and no more honest.

CREATE SCHEMA IF NOT EXISTS public;

DROP TABLE IF EXISTS public.apply_queue;
CREATE TABLE public.apply_queue (
    -- identity: (source, job_id). Deliberately NO primary key, because the real
    -- dbt table has none - Posting declares the composite key at the model level,
    -- and a spec passing against a table WITH a key would not prove that works.
    source                TEXT NOT NULL,
    job_id                TEXT NOT NULL,
    country               TEXT,
    company_name          TEXT,
    job_title             TEXT,
    location_raw          TEXT,
    location_state        TEXT,
    posted_date           DATE,
    days_since_posted     INTEGER,
    seniority             TEXT,
    locations_posted      INTEGER,
    salary_min_reported   NUMERIC,
    redirect_url          TEXT,
    link_url              TEXT,
    refuses_sponsorship   BOOLEAN,
    offers_sponsorship    BOOLEAN,
    link_tier             TEXT,
    eligibility           TEXT,
    eligibility_reason    TEXT,
    sponsorship_signal    TEXT,
    visa_reasoning        TEXT,
    fit_score             INTEGER,
    fit_reasoning         TEXT,
    tech_stack            TEXT[],
    concerns              TEXT,
    enriched_at           TIMESTAMPTZ,
    sponsorship_status    TEXT,
    sponsor_filings       INTEGER,
    sponsor_certified_pct NUMERIC,
    sponsor_median_wage   NUMERIC,
    sponsor_latest_year   INTEGER,
    heuristic_score       NUMERIC,
    posting_sponsorship   TEXT,
    recommendation        TEXT,
    apply_rank            INTEGER
);

DROP TABLE IF EXISTS public.outreach_tracker;
CREATE TABLE public.outreach_tracker (
    source           TEXT NOT NULL,
    job_id           TEXT NOT NULL,
    company_name     TEXT,
    job_title        TEXT,
    status           TEXT NOT NULL DEFAULT 'not_contacted',
    draft_email      TEXT,
    draft_connection TEXT,
    draft_followup   TEXT,
    draft_source     TEXT,
    notes            TEXT,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, job_id)
);
