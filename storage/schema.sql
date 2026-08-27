-- Signal warehouse schema.
-- Applied automatically the first time the Postgres container is created.

CREATE TABLE IF NOT EXISTS raw_postings (
    -- Natural key: a job id is only unique within its source, so both
    -- columns together identify a posting.
    source              TEXT        NOT NULL,
    job_id              TEXT        NOT NULL,

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

    PRIMARY KEY (source, job_id)
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
