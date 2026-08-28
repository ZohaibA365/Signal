-- Minimal fixture so dbt models have something to build against in CI.
--
-- CI runs with no secrets and no access to the real warehouse, so it needs its
-- own data. A handful of rows is enough: the point of the CI build is to catch
-- SQL that does not compile, models whose grain has broken, and tests whose
-- assertions no longer hold - none of which need production volume.
--
-- The rows are chosen to exercise the logic that has actually broken before:
-- an internship and a senior role (seniority regex), a posting whose title
-- carries a city (the dedup macro), a predicted salary (which must never reach
-- salary_min_reported), and a null company (which staging must drop).

INSERT INTO raw_postings
    (source, job_id, country, company_name, job_title, location, location_state,
     posted_date, salary_min, salary_is_predicted, description_raw, category,
     search_term, ingested_at)
VALUES
    ('adzuna','ci-1','us','Acme Data','Data Engineer Intern','Austin, TX','Texas',
     now() - interval '2 days', 90000, false,
     'Build pipelines with Python, dbt and Snowflake. Airflow experience a plus.',
     'IT Jobs','data engineer intern', now()),

    ('adzuna','ci-2','us','Acme Data','Senior Staff Data Engineer','Austin, TX','Texas',
     now() - interval '5 days', 210000, true,
     'Lead the data platform team. Spark, Kafka and Kubernetes at scale.',
     'IT Jobs','data engineer', now()),

    -- Same role, city inside the title: the normalised_title macro must
    -- collapse these two into one opportunity.
    ('adzuna','ci-3','us','Globex','Analytics Engineer - Boston, MA','Boston, MA','Massachusetts',
     now() - interval '1 day', NULL, NULL,
     'Analytics engineering with dbt and BigQuery.','IT Jobs','analytics engineer', now()),
    ('adzuna','ci-4','us','Globex','Analytics Engineer - Denver, CO','Denver, CO','Colorado',
     now() - interval '1 day', NULL, NULL,
     'Analytics engineering with dbt and BigQuery.','IT Jobs','analytics engineer', now()),

    -- Must be dropped by staging: a posting with no employer is unusable.
    ('adzuna','ci-5','us',NULL,'Data Analyst','US',NULL,
     now() - interval '3 days', NULL, NULL,'Anonymous posting.','IT Jobs','data analyst', now()),

    ('company_board','ci-6','ca','Maple Systems','Data Engineering Co-op','Toronto, Ontario','Ontario',
     now() - interval '4 days', NULL, NULL,
     'Co-op building ETL with Python and Postgres.','Engineering','Maple Systems', now());

INSERT INTO posting_technologies (source, job_id, tech_slug) VALUES
    ('adzuna','ci-1','python'), ('adzuna','ci-1','dbt'), ('adzuna','ci-1','snowflake'),
    ('adzuna','ci-2','spark'),  ('adzuna','ci-2','kafka'),
    ('adzuna','ci-3','dbt'),    ('adzuna','ci-4','dbt'),
    ('company_board','ci-6','python'), ('company_board','ci-6','postgresql');

INSERT INTO market_snapshots
    (snapshot_date, tech_slug, tech_name, category, search_query, openings)
VALUES
    (current_date,'python','Python','language','Python',140000),
    (current_date,'snowflake','Snowflake','warehouse','Snowflake',15000),
    (current_date,'databricks','Databricks','warehouse','Databricks',17000),
    (current_date,'dbt','dbt','transform','dbt analytics',430);

INSERT INTO job_enrichment
    (source, job_id, eligibility, eligibility_reason, sponsorship_signal,
     visa_reasoning, fit_score, fit_reasoning, tech_stack, concerns,
     model, description_hash, profile)
VALUES
    ('adzuna','ci-1','eligible','Text states sponsorship is available.','sponsors',
     'Posting mentions visa sponsorship.',82,'Strong term and skill match.',
     ARRAY['python','dbt'],ARRAY[]::text[],'ci-fixture','hash1','student');

-- Sponsorship fixture: one employer that matches exactly and one that only
-- matches by legal-entity prefix, so the confidence grading is exercised.
INSERT INTO dol_employer_summary
    (employer_key, fiscal_year, employer_name, filings, certified, certified_pct,
     tech_filings, tech_pct, distinct_titles, distinct_states, median_wage,
     p25_wage, p75_wage, max_wage, tech_soc_titles, rank_in_year)
VALUES
    ('ACME DATA','2026','Acme Data Inc.',40,40,100.0,30,75.0,12,4,
     150000,130000,180000,220000,ARRAY['Software Developers'],12),
    ('GLOBEX ANALYTICS','2026','Globex Analytics LLC',5,4,80.0,3,60.0,3,1,
     120000,110000,140000,160000,ARRAY['Data Scientists'],400);

INSERT INTO company_employer_key (company_name, employer_key, match_type) VALUES
    ('Acme Data','ACME DATA','exact'),
    ('Globex','GLOBEX ANALYTICS','prefix_strong'),
    ('Maple Systems', NULL, NULL);
