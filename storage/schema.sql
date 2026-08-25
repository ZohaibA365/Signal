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
