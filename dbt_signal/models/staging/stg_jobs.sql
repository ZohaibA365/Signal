{{ config(materialized='view') }}

/*
  Cleans and standardises raw postings.

  Two things happen here that the raw layer deliberately did not do:
  1. Deduplication. The same role is posted repeatedly under different job_ids
     (Booz Allen's "Data Engineer" in Chantilly appears 7 times), so identical
     company + title + location collapses to the most recently posted row.
  2. Derived attributes - seniority, staleness, and whether a salary is a real
     posted figure or one of Adzuna's model estimates.

  PORTABILITY: written to run on both Postgres and Snowflake. FILTER (WHERE)
  is Postgres-only, so counts use CASE WHEN; regex uses regexp_like(), which
  both engines support, rather than the Postgres-only ~* operator.

  REGEX PORTABILITY, two separate traps:

  1. Anchoring. Postgres's regexp_like searches anywhere in the string;
     Snowflake's must match the WHOLE value. Identical SQL classified 1,590
     postings as internships on Postgres and 0 on Snowflake - compiling
     cleanly on both while silently disagreeing.
  2. Word boundaries. Postgres spells them \y; Snowflake has no equivalent
     (\b is a backspace in Postgres ARE), so there is no shared escape. They
     are written as explicit ([^a-z]|^) character classes instead, which also
     keeps "internal" from matching "intern".

  Patterns are wrapped in .* deliberately. Postgres's
  regexp_like searches for a match anywhere in the string; Snowflake's is
  anchored and must match the WHOLE value. Identical SQL therefore classified
  1,590 postings as internships on Postgres and 0 on Snowflake - it compiled
  cleanly on both and silently produced different answers. The wrapped form
  behaves the same everywhere.
*/

with source as (

    select * from {{ source('signal', 'raw_postings') }}

),

cleaned as (

    select
        source,
        job_id,
        country,
        nullif(trim(company_name), '')          as company_name,
        nullif(trim(job_title), '')             as job_title,
        location                                as location_raw,
        location_state,
        split_part(location, ',', 1)            as location_city,
        posted_date,
        (current_date - posted_date::date)      as days_since_posted,

        salary_min,
        salary_max,
        salary_is_predicted,
        -- Only ~1% of these are real. Surfacing an estimate as fact would
        -- actively mislead, so keep the reported figure separate.
        case when salary_is_predicted then null else salary_min end as salary_min_reported,

        description_raw,
        category,
        redirect_url,
        search_term,
        ingested_at,
        first_seen,
        last_seen,

        -- Order matters: "Senior Associate" must resolve to senior, not entry.
        -- \y is a word boundary, without which "Internal" matches "intern".
        case
            when regexp_like(job_title, '^(.*[^a-z])?(intern|interns|internship|co-?op)([^a-z].*)?$', 'i') then 'intern'
            when regexp_like(job_title, '^(.*[^a-z])?(senior|sr|staff|principal|lead|distinguished|manager|director|head|vp|chief|architect|expert)([^a-z].*)?$', 'i') then 'senior'
            when regexp_like(job_title, '^(.*[^a-z])?(new grad|graduate|entry.level|junior|jr|associate|apprentice)([^a-z].*)?$', 'i') then 'entry'
            else 'mid'
        end as seniority,

        regexp_like(job_title, '^(.*[^a-z])?(intern|interns|internship|co-?op)([^a-z].*)?$', 'i') as is_internship,

        -- Adzuna keeps postings live long after they are realistically open.
        (current_date - posted_date::date) > 60  as is_stale

    from source
    -- A handful of postings (9 of 20,084) carry no employer at all - Adzuna
    -- returns them anonymised, with location flattened to just "US". They
    -- cannot be applied to and cannot be attributed to a company, so they are
    -- useless downstream. The raw layer keeps them; staging drops them.
    where nullif(trim(company_name), '') is not null

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by lower(coalesce(company_name, '')),
                         lower(coalesce(job_title, '')),
                         lower(coalesce(location_raw, ''))
            order by posted_date desc nulls last, job_id
        ) as _row_num
    from cleaned

)

select
    source,
    job_id,
    company_name,
    job_title,
    location_raw,
    location_state,
    location_city,
    posted_date,
    days_since_posted,
    salary_min,
    salary_max,
    salary_is_predicted,
    salary_min_reported,
    description_raw,
    category,
    redirect_url,

    /*
      Where "Apply" actually sends someone, and whether it can be trusted.

      Adzuna supplies 90% of this corpus and its redirect_url is a
      country-gated adzuna.com link: opened from outside the posting's own
      country it shows "not available in your region", and it 403s to
      anything that is not a browser, so it cannot be resolved to the real
      posting either. Adzuna's API never exposes the employer's own URL, so
      these are unfixable rather than merely unfixed.

      A career-board posting carries the employer's own URL by construction.
      Only those are linkable, so only those reach the job list; the rest
      still count toward market statistics, where no link is involved.
    */
    case when source = 'company_board' then redirect_url end   as link_url,
    case when source = 'company_board' then 'direct'
         else 'aggregator' end                                 as link_tier,

    search_term,
    ingested_at,
    first_seen,
    last_seen,
    seniority,
    is_internship,
    is_stale,
    country
from deduplicated
where _row_num = 1
