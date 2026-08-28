{{ config(materialized='table') }}

/*
  The morning list: what to actually apply to, in order.

  ranked_opportunities scores on structured signals alone - freshness,
  seniority, salary. This model layers the LLM assessment on top and reorders
  by it, because eligibility dominates everything else: a role requiring US
  citizenship is worth zero to this candidate no matter how fresh or well-paid.

  Roles not yet scored are kept with a null fit_score rather than dropped, so
  a partial enrichment run never silently hides opportunities.
*/

with opportunities as (

    select * from {{ ref('ranked_opportunities') }}

),

enrichment as (

    select * from {{ source('signal_ai', 'job_enrichment') }}

),

joined as (

    select
        o.source,
        o.job_id,
        o.country,
        o.company_name,
        o.job_title,
        o.location_raw,
        o.location_state,
        o.posted_date,
        o.days_since_posted,
        o.seniority,
        o.locations_posted,
        o.salary_min_reported,
        o.redirect_url,
        o.description_raw,

        e.eligibility,
        e.eligibility_reason,
        e.sponsorship_signal,
        e.visa_reasoning,
        e.fit_score,
        e.fit_reasoning,
        e.tech_stack,
        e.concerns,
        e.enriched_at,

        -- Structured score from the SQL layer, kept for comparison against
        -- the LLM's judgement.
        o.opportunity_score as heuristic_score

    from opportunities o
    left join enrichment e
           on e.source = o.source and e.job_id = o.job_id

),

final as (

    select
        *,
        case
            when eligibility = 'blocked'  then 'skip'
            when fit_score is null        then 'not yet scored'
            when fit_score >= 70          then 'apply now'
            when fit_score >= 45          then 'worth a look'
            else 'low priority'
        end as recommendation
    from joined

)

select
    *,
    row_number() over (
        order by
            -- Blocked roles sink regardless of score.
            case when eligibility = 'blocked' then 1 else 0 end,
            fit_score desc nulls last,
            days_since_posted asc
    ) as apply_rank
from final
