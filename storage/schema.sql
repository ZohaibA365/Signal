-- Signal warehouse schema.
-- Applied automatically the first time the Postgres container is created.

CREATE TABLE IF NOT EXISTS raw_postings (
    -- Natural key: a job id is only unique within its source, so both
    -- columns together identify a posting.
    source              TEXT        NOT NULL,
    job_id              TEXT        NOT NULL,
    -- Adzuna has one endpoint per country and quotes salary in that
    -- country's currency, so CAD and USD figures must never be mixed.
    country             TEXT        NOT NULL DEFAULT 'us',

    company_name        TEXT,
    job_title           TEXT,
    location            TEXT,
    posted_date         TIMESTAMPTZ,
    salary_min          NUMERIC,
    salary_max          NUMERIC,
    -- Adzuna estimates salary with a model when the posting omits it.
    -- Without this flag the dashboard would present guesses as fact.
    salary_is_predicted BOOLEAN,
    description_raw     TEXT,
    category            TEXT,
    redirect_url        TEXT,
    latitude            NUMERIC,
    longitude           NUMERIC,
    -- Adzuna's location hierarchy, e.g. ["US","Tennessee","Davidson County","Nashville"].
    -- The flat `location` string only gives city+county, so state lives here.
    location_state      TEXT,
    location_area       JSONB,

    -- Provenance: which query surfaced this posting, and when we saw it.
    search_term         TEXT,
    ingested_at         TIMESTAMPTZ NOT NULL,

    -- Change tracking. last_seen is what powers the "gone cold" flag:
    -- a posting that stops appearing in the feed has likely been filled.
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (source, job_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_postings_company     ON raw_postings (company_name);
CREATE INDEX IF NOT EXISTS idx_raw_postings_posted_date ON raw_postings (posted_date DESC);
CREATE INDEX IF NOT EXISTS idx_raw_postings_last_seen   ON raw_postings (last_seen DESC);

-- LLM enrichment output, kept in its own table rather than as columns on
-- raw_postings. Enrichment is expensive and slow; the raw layer must stay
-- cheap to rebuild. Keying on description_hash lets a re-run skip any
-- posting whose text has not changed since it was last scored.
CREATE TABLE IF NOT EXISTS job_enrichment (
    source              TEXT        NOT NULL,
    job_id              TEXT        NOT NULL,
    -- Adzuna has one endpoint per country and quotes salary in that
    -- country's currency, so CAD and USD figures must never be mixed.
    country             TEXT        NOT NULL DEFAULT 'us',

    -- Can this candidate legally hold the role at all?
    eligibility         TEXT,   -- eligible | blocked | unclear
    eligibility_reason  TEXT,

    -- What the posting says about sponsoring work authorisation.
    sponsorship_signal  TEXT,   -- sponsors | no_sponsorship | unclear
    visa_reasoning      TEXT,

    fit_score           INTEGER CHECK (fit_score BETWEEN 0 AND 100),
    fit_reasoning       TEXT,
    tech_stack          TEXT[],
    concerns            TEXT[],

    model               TEXT        NOT NULL,
    description_hash    TEXT        NOT NULL,
    enriched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Which candidate profile produced this assessment. Signal supports more
    -- than one (see ai_layer/profile.py), and a score is only meaningful with
    -- respect to the profile it was scored against - a role that is a 5 for a
    -- student can be an 85 for an experienced hire. Without this in the key,
    -- scoring a second profile silently overwrites the first.
    profile             TEXT        NOT NULL DEFAULT 'student',

    PRIMARY KEY (source, job_id, profile)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_fit ON job_enrichment (fit_score DESC);

-- ---------------------------------------------------------------------------
-- Market observatory tables.
--
-- These hold the daily demand snapshot that makes Signal more than a job
-- board. Adzuna's `history` endpoint only covers recognised job categories -
-- verified: it returns 12 months for "data engineer" and nothing at all for
-- "snowflake". So a per-technology time series does not exist anywhere and
-- has to be accumulated one day at a time. That accumulation is the asset:
-- cloning this repo gets you the code, not the history.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_date  DATE        NOT NULL,
    tech_slug      TEXT        NOT NULL,
    tech_name      TEXT        NOT NULL,
    category       TEXT        NOT NULL,
    search_query   TEXT        NOT NULL,
    openings       INTEGER     NOT NULL,
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, tech_slug)
);

CREATE INDEX IF NOT EXISTS idx_msnap_tech ON market_snapshots (tech_slug, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_msnap_cat  ON market_snapshots (category, snapshot_date);

-- Which employers dominate hiring for a given technology.
CREATE TABLE IF NOT EXISTS market_snapshot_companies (
    snapshot_date  DATE        NOT NULL,
    tech_slug      TEXT        NOT NULL,
    company_name   TEXT        NOT NULL,
    postings       INTEGER     NOT NULL,
    rank           INTEGER     NOT NULL,
    PRIMARY KEY (snapshot_date, tech_slug, company_name)
);

CREATE INDEX IF NOT EXISTS idx_msnap_co ON market_snapshot_companies (company_name, snapshot_date);

-- Salary distribution per technology, as bucket -> posting count.
CREATE TABLE IF NOT EXISTS market_snapshot_salary (
    snapshot_date  DATE     NOT NULL,
    tech_slug      TEXT     NOT NULL,
    salary_bucket  INTEGER  NOT NULL,
    posting_count  INTEGER  NOT NULL,
    PRIMARY KEY (snapshot_date, tech_slug, salary_bucket)
);

-- Technology mentions per posting, produced by ai_layer/extract_tech.py.
-- This is the bridge between the Python taxonomy and the dbt models: dbt
-- cannot call the matcher, so matches are materialised here and joined.
CREATE TABLE IF NOT EXISTS posting_technologies (
    source      TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    tech_slug   TEXT NOT NULL,
    PRIMARY KEY (source, job_id, tech_slug)
);

CREATE INDEX IF NOT EXISTS idx_posting_tech_slug ON posting_technologies (tech_slug);

-- ---------------------------------------------------------------------------
-- Tables that were previously created ad hoc by the scripts that populate
-- them. That worked on a warehouse built up over time and failed on a fresh
-- one: CI applies this file and then builds dbt, so any table a model reads
-- has to be declared here or the build fails on a missing relation.
-- schema.sql is the single source of truth for structure; scripts only load.
-- ---------------------------------------------------------------------------

-- DOL H-1B filings aggregated to employer-year by processing/dol_spark.py.
CREATE TABLE IF NOT EXISTS dol_employer_summary (
    employer_key     TEXT    NOT NULL,
    fiscal_year      TEXT    NOT NULL,
    employer_name    TEXT,
    filings          INTEGER NOT NULL,
    certified        INTEGER,
    certified_pct    NUMERIC,
    tech_filings     INTEGER,
    tech_pct         NUMERIC,
    distinct_titles  INTEGER,
    distinct_states  INTEGER,
    median_wage      NUMERIC,
    p25_wage         NUMERIC,
    p75_wage         NUMERIC,
    max_wage         NUMERIC,
    tech_soc_titles  TEXT[],
    rank_in_year     INTEGER,
    PRIMARY KEY (employer_key, fiscal_year)
);
CREATE INDEX IF NOT EXISTS idx_dol_employer ON dol_employer_summary (employer_key);

-- Posting company name to DOL employer key. match_type grades confidence:
-- only 'exact' and 'prefix_strong' may be stated as fact, because sampling
-- found weak prefix matches pairing unrelated organisations.
CREATE TABLE IF NOT EXISTS company_employer_key (
    company_name TEXT PRIMARY KEY,
    employer_key TEXT,
    match_type   TEXT
);
CREATE INDEX IF NOT EXISTS idx_company_employer_key ON company_employer_key (employer_key);

-- Every DOL employer a company could plausibly be, so an ambiguous case can be
-- reviewed rather than guessed.
--
-- The matcher used to take the lexicographically first candidate whenever a
-- brand prefixed several legal entities. "Cognizant" resolved to COGNIZANT
-- MOBILITY with 13 filings instead of COGNIZANT TECHNOLOGY SOLUTIONS with
-- 15,274, out of 12 candidates, and only escaped publication because COGNIZANT
-- has no space and so graded prefix_weak. Filing volume is not the tiebreak
-- either: "Lucid Motors" prefixes LUCID with 842 filings, a different company
-- from Lucid Group with 15. So ambiguity is recorded, not resolved.
-- Which DOL legal entities belong to one employer. One row per link, because a
-- company can own several, and sponsorship totals are summed over this rather
-- than read from a single key.
--
-- company_employer_key answers "what do we know about this company" and holds
-- one row each. This answers "which entities are it". Reading one key
-- understated the largest employers by two to six times: Capital One files as
-- CAPITAL ONE SERVICES (1,029 filings) and CAPITAL ONE NATIONAL ASSOCIATION
-- (523), PwC across five entities totalling 1,778, Cognizant across four
-- totalling 15,355.
CREATE TABLE IF NOT EXISTS company_employer_link (
    company_name TEXT NOT NULL,
    employer_key TEXT NOT NULL,
    match_type   TEXT NOT NULL,

    PRIMARY KEY (company_name, employer_key)
);
CREATE INDEX IF NOT EXISTS idx_company_employer_link_company
    ON company_employer_link (company_name);

CREATE TABLE IF NOT EXISTS company_employer_candidates (
    company_name TEXT    NOT NULL,
    employer_key TEXT    NOT NULL,
    filings      INTEGER,

    PRIMARY KEY (company_name, employer_key)
);

-- Streaming: what the producer has already published. Deliberately a
-- watermark over first_seen rather than consumer offsets - offsets track what
-- was read, this tracks what exists, which is what survives a restart.
CREATE TABLE IF NOT EXISTS streaming_watermark (
    stream_name  TEXT PRIMARY KEY,
    last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS posting_alerts (
    source        TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    company_name  TEXT,
    job_title     TEXT,
    reason        TEXT,
    screen_score  INTEGER,
    sponsorship   TEXT,
    alerted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, job_id)
);

-- Which applicant-tracking system each company's board lives on.
--
-- This exists because board coordinates cannot be guessed. Slug-guessing
-- against Greenhouse/Lever/Ashby resolved only 5 of the 60 largest employers
-- in the corpus - the rest are on Workday, whose tenant host and site path
-- are both arbitrary (Capital One is capitalone.wd12 with site Capital_One,
-- not wd1, not Careers). SmartRecruiters is the same story: BoschGroup, not
-- bosch. So coordinates are read off a real careers URL and recorded here.
--
-- Failures are cached as deliberately as successes. A sweep over thousands of
-- employers must never re-probe a name it has already answered, or it burns
-- politeness budget it cannot afford and can never be resumed.
CREATE TABLE IF NOT EXISTS board_registry (
    company_name    TEXT        PRIMARY KEY,
    -- NULL means "looked, found nothing" - a real answer, not a missing one.
    ats             TEXT,
    -- Shape differs by system: {slug} for the startup boards, and
    -- {tenant, host, site} for Workday. Kept as JSON so a new adapter needs
    -- no migration.
    coords          JSONB,

    status          TEXT        NOT NULL,   -- resolved | not_found | error
    -- How far the identity check got. A guessed slug that the board itself
    -- confirms by name is trustworthy; one that only matches on tokens is a
    -- guess we are choosing to accept. Attaching another company's jobs to a
    -- page is worse than showing none, so the grade travels with the entry.
    confidence      TEXT,                   -- manual | name_verified | token_match
    discovered_via  TEXT,                   -- manual | guess
    postings_seen   INTEGER,
    note            TEXT,

    first_tried_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verified_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_board_registry_status ON board_registry (status);

-- When each board was last fetched, so a daily run can refresh the stalest
-- boards within a time budget instead of walking all of them.
--
-- The registry grew from 32 companies to 1,624, and a full pass went from
-- minutes to about 85 of them. The scheduled pipeline allows 45, so it began
-- timing out at the ingest step every morning and skipping the 10 steps after
-- it - the warehouse, the transform and the site all stopped moving while the
-- run still looked like it was doing something.
ALTER TABLE board_registry ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_board_registry_staleness
    ON board_registry (last_ingested_at NULLS FIRST);

-- ---------------------------------------------------------------------------
-- History summaries, computed by analytics/build_history.py over the S3
-- archive and written back here so the site keeps reading only one database.
--
-- They are summaries on purpose. The panel they come from is one row per
-- posting per day - around 54,000 rows a day, 18M a year - which is exactly
-- the shape this 512 MB warehouse cannot hold and the reason the archive lives
-- in S3. What comes back is a few thousand rows.

-- The authoritative answer to "how much history is there".
--
-- Two constants called MIN_DAYS_FOR_TREND already exist, in site/build.py and
-- outreach/insights.py, and they measure different things: distinct
-- market_snapshots.snapshot_date and distinct raw_postings.first_seen::date.
-- Neither asks whether a day's posting panel is complete, which is the only
-- question that matters before dividing one window by another. A backfill
-- recovers two sightings per posting and nothing between them, so most dates
-- in the panel are undercounts. measured_days counts only dates a real daily
-- archive run covered.
CREATE TABLE IF NOT EXISTS hist_coverage (
    id                          INTEGER PRIMARY KEY,    -- always 1; one row
    measured_days               INTEGER NOT NULL,
    panel_days                  INTEGER NOT NULL,
    first_measured              DATE,
    last_measured               DATE,
    min_measured_days_required  INTEGER NOT NULL,
    trend_is_publishable        BOOLEAN NOT NULL,
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Roles observed open per day. Measured days only: a gap in a series is
-- visible, but an undercount reads as a decline.
CREATE TABLE IF NOT EXISTS hist_daily_roles (
    observed_date   DATE    NOT NULL,
    country         TEXT    NOT NULL,
    open_roles      INTEGER NOT NULL,
    companies       INTEGER NOT NULL,

    PRIMARY KEY (observed_date, country)
);

-- Per company, from the panel rather than from posted_date.
--
-- median_days_open counts only postings that have stopped appearing, because a
-- posting still open has no end date yet and one that predates collection looks
-- younger than it is. closed_share says how much of the sample that median
-- rests on.
CREATE TABLE IF NOT EXISTS hist_company_pace (
    company_name        TEXT    NOT NULL PRIMARY KEY,
    country             TEXT,
    postings_observed   INTEGER NOT NULL,
    first_observed      DATE,
    last_observed       DATE,
    distinct_open_days  INTEGER,
    median_days_open    NUMERIC,
    closed_share        NUMERIC,

    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Technology mentions among roles actually observed open each day. The
-- unbiased counterpart to tech_demand_history, which reconstructs months from
-- posted_date over surviving postings and says so in its own header.
CREATE TABLE IF NOT EXISTS hist_tech_daily (
    observed_date        DATE    NOT NULL,
    tech_slug            TEXT    NOT NULL,
    postings_mentioning  INTEGER NOT NULL,

    PRIMARY KEY (observed_date, tech_slug)
);
