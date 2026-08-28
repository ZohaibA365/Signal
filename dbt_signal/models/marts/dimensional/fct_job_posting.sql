{{ config(materialized='table') }}

/*
  Job posting fact. Grain: one row per posting.

  Carries foreign keys and measures only - every descriptive attribute lives
  in a dimension. This is the change that removes company and technology
  detail being repeated across tens of thousands of rows.

  Note the grain is the POSTING, not the deduplicated role. ranked_opportunities
  collapses a role posted across many cities into one row, which is right for a
  ranked feed but wrong for a fact table: counting how much a company is hiring
  needs every req.
*/

with postings as (

    select * from {{ ref('stg_jobs') }}

),

enrichment as (

    select source, job_id, profile, fit_score, eligibility, sponsorship_signal
    from {{ source('signal_ai', 'job_enrichment') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['p.source', 'p.job_id']) }} as posting_key,
    {{ dbt_utils.generate_surrogate_key(['p.company_name']) }}       as company_key,
    {{ dbt_utils.generate_surrogate_key(['p.posted_date::date']) }}  as posted_date_key,

    p.source,
    p.job_id,
    p.country,
    p.location_state,
    p.seniority,

    -- Measures
    1                                as posting_count,
    p.days_since_posted,
    p.salary_min_reported,           -- employer-stated only; estimates excluded upstream
    e.fit_score,

    -- Degenerate dimensions: low cardinality, no separate table earns its keep
    e.eligibility,
    e.sponsorship_signal,
    e.profile                        as scored_for_profile,
    p.is_internship,
    p.is_stale,
    p.salary_min_reported is not null as has_stated_salary

from postings p
left join enrichment e
       on e.source = p.source and e.job_id = p.job_id
