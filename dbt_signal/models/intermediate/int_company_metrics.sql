{{ config(materialized='view') }}

/*
  Company-level hiring signals, aggregated from postings.

  Hiring velocity is the useful one: a company posting many distinct roles
  across a short window is actively scaling, which usually means faster
  processes and more openings than the single posting you happened to see.

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

with jobs as (

    select * from {{ ref('stg_jobs') }}

),

metrics as (

    select
        company_name,

        count(*)                                            as total_postings,
        count(distinct job_title)                           as distinct_titles,
        count(distinct location_state)                      as states_hiring_in,

        min(posted_date)                                    as first_posting_at,
        max(posted_date)                                    as latest_posting_at,

        count(case when not is_stale then 1 end)                as active_postings,
        count(case when is_internship then 1 end)               as internship_postings,
        count(case when seniority = 'entry' then 1 end)         as entry_level_postings,

        round(avg(salary_min))                              as avg_salary_min,
        count(salary_min_reported)                          as postings_with_real_salary

    from jobs
    group by company_name

)

select
    *,

    -- Distinct roles per week over the window this company has been posting in.
    -- greatest(..., 1) guards against divide-by-zero when every posting landed
    -- on the same day.
    round(
        distinct_titles::numeric
        / greatest(extract(day from (latest_posting_at - first_posting_at)) / 7.0, 1)
    , 2) as roles_per_week,

    (current_date - latest_posting_at::date) as days_since_last_posting

from metrics
