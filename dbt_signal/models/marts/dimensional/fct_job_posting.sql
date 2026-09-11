{{
  config(
    materialized='incremental',
    unique_key='posting_key',
    incremental_strategy='delete+insert',
    post_hook="""
      {% if is_incremental() %}
      -- Remove facts whose posting no longer exists upstream.
      --
      -- delete+insert only deletes keys present in the INCOMING batch, so a
      -- posting that disappears from the source - an aggregator purged, a
      -- company renamed, a row deleted - leaves its fact row behind forever.
      -- Two such orphans (WorkOS, CAPITAL ONE) failed the referential
      -- integrity test against dim_company, which is rebuilt in full each run
      -- and so had already forgotten them. The fact table has to forget too.
      delete from {{ this }} f
      where not exists (
          select 1 from {{ ref('stg_jobs') }} s
          where s.source = f.source and s.job_id = f.job_id
      );

      -- And remove facts pointing at a company that no longer exists.
      --
      -- The clause above does not cover this, despite its comment naming a
      -- rename as a cause. company_key is a surrogate hash of company_name, so
      -- merging two spellings of one employer changes the key while
      -- (source, job_id) stays put: the posting still exists, the orphan check
      -- passes, and the row keeps a key dim_company has forgotten. 626 rows
      -- failed referential integrity that way the first time names were merged.
      --
      -- Deleting them is correct but not sufficient alone. This table is
      -- incremental on updated_at, so a deleted row only returns once its
      -- posting is touched again; after a canonicalisation change, a
      -- full-refresh of this model restores them.
      --
      -- No double quotes in this comment on purpose: the post_hook is parsed as
      -- a Jinja config value and a quoted phrase here ends the string early.
      delete from {{ this }} f
      where not exists (
          select 1 from {{ ref('dim_company') }} c
          where c.company_key = f.company_key
      )
      {% endif %}
    """
  )
}}

{# dim_company is referenced only inside the post-hook's is_incremental()
   block, so dbt cannot infer the dependency from the model body and will not
   guarantee dim_company is built first. Declaring it here does both: it fixes
   the compile error and makes the build order explicit, which matters because
   the hook deletes facts by comparing against that table. #}
-- depends_on: {{ ref('dim_company') }}

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
