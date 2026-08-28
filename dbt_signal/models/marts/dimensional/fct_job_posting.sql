{{
  config(
    materialized='incremental',
    unique_key='posting_key',
    incremental_strategy='delete+insert'
  )
}}

/*
  Job posting fact. Grain: one row per posting.

  Carries foreign keys and measures only - every descriptive attribute lives
  in a dimension. This is what removes company and technology detail being
  repeated across tens of thousands of rows.

  Grain note: this is the POSTING, not the deduplicated role.
  ranked_opportunities collapses a role posted across many cities into one
  row, which is right for a ranked feed but wrong for a fact table - measuring
  how much a company is hiring needs every req.

  INCREMENTAL WATERMARK. The obvious watermark is last_seen, and it is wrong
  on its own: an LLM assessment lands hours or days after the posting was
  loaded, without touching last_seen. Filtering on last_seen alone would leave
  every newly scored posting stuck with a null fit_score until the next full
  refresh - the table would look fine and quietly be stale. The watermark is
  therefore the later of the two timestamps.
*/

with postings as (

    select * from {{ ref('stg_jobs') }}

),

enrichment as (

    select source, job_id, profile, fit_score, eligibility, sponsorship_signal,
           enriched_at
    from {{ source('signal_ai', 'job_enrichment') }}

),

joined as (

    select
        p.*,
        e.profile,
        e.fit_score,
        e.eligibility,
        e.sponsorship_signal,
        -- Row changes when either the posting is re-seen or it is re-scored.
        greatest(p.last_seen, coalesce(e.enriched_at, p.last_seen)) as updated_at
    from postings p
    left join enrichment e
           on e.source = p.source and e.job_id = p.job_id

)

select
    {{ dbt_utils.generate_surrogate_key(['source', 'job_id']) }} as posting_key,
    {{ dbt_utils.generate_surrogate_key(['company_name']) }}     as company_key,
    {{ dbt_utils.generate_surrogate_key(['posted_date::date']) }} as posted_date_key,

    source,
    job_id,
    country,
    location_state,
    seniority,

    -- Measures
    1                        as posting_count,
    days_since_posted,
    salary_min_reported,     -- employer-stated only; estimates excluded upstream
    fit_score,

    -- Degenerate dimensions: low cardinality, no separate table earns its keep
    eligibility,
    sponsorship_signal,
    profile                  as scored_for_profile,
    is_internship,
    is_stale,
    salary_min_reported is not null as has_stated_salary,

    updated_at

from joined

{% if is_incremental() %}
  -- Only rows touched since the last build. The subquery is evaluated once.
  where updated_at > (select coalesce(max(updated_at), '1900-01-01'::timestamptz) from {{ this }})
{% endif %}
