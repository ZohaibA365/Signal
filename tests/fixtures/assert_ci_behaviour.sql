-- Behavioural assertions run by CI after the dbt build.
--
-- Kept in a file rather than inlined in the workflow: a DO $$ ... $$ block
-- inside YAML inside a shell double-quoted -c argument has three layers of
-- escaping, and getting any of them wrong fails in a way the logs make hard
-- to read. A file has none of that.
--
-- These check the behaviours that have actually regressed before, not
-- generic invariants.

DO $$
DECLARE n int;
BEGIN
  -- A posting with no employer cannot be applied to or attributed.
  SELECT count(*) INTO n FROM stg_jobs WHERE company_name IS NULL;
  IF n <> 0 THEN RAISE EXCEPTION 'null-company posting survived staging (% rows)', n; END IF;

  -- Two postings differing only by a city inside the title are one opportunity.
  SELECT count(*) INTO n FROM ranked_opportunities WHERE company_name = 'Globex';
  IF n <> 1 THEN RAISE EXCEPTION 'title dedup failed: expected 1 Globex role, got %', n; END IF;

  -- ~99% of source salaries are model estimates; one must never be presented
  -- as an employer-stated figure.
  SELECT count(*) INTO n FROM stg_jobs
   WHERE salary_min_reported IS NOT NULL AND salary_is_predicted;
  IF n <> 0 THEN RAISE EXCEPTION 'estimated salary leaked into reported (% rows)', n; END IF;

  -- The seniority regex has broken twice: once on Postgres/Snowflake anchoring
  -- differences, once on word boundaries matching "Internal" as "intern".
  SELECT count(*) INTO n FROM stg_jobs WHERE seniority = 'intern';
  IF n < 2 THEN RAISE EXCEPTION 'seniority regex missed internships (got %)', n; END IF;

  SELECT count(*) INTO n FROM stg_jobs WHERE seniority = 'senior';
  IF n < 1 THEN RAISE EXCEPTION 'seniority regex missed senior roles'; END IF;

  -- Sponsorship confidence must survive into the model.
  SELECT count(*) INTO n FROM int_company_sponsorship WHERE is_confident_match;
  IF n < 2 THEN RAISE EXCEPTION 'sponsorship confidence grading lost (% rows)', n; END IF;

  RAISE NOTICE 'all behavioural assertions passed';
END $$;
