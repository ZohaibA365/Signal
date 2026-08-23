{{ config(materialized='table') }}

/*
  The table the dashboard reads.

  This produces a heuristic ranking from structured signals only - freshness,
  seniority, and company hiring activity. It intentionally contains no LLM
  output yet; the Claude enrichment layer (visa detection and profile fit)
  lands in Phase 2 and will join onto this on job_id.

  Keeping the deterministic ranking separate from the LLM scoring means the
  dashboard still works if the API is down, and makes it possible to measure
  how much the LLM actually improves ordering.
*/

with jobs as (

    select * from {{ ref('stg_jobs') }}

),

companies as (

    select * from {{ ref('int_company_metrics') }}

),

joined as (

    select
        j.*,
        c.total_postings          as company_total_postings,
        c.active_postings         as company_active_postings,
        c.roles_per_week          as company_roles_per_week,
        c.states_hiring_in        as company_states_hiring_in,
        c.internship_postings     as company_internship_postings
    from jobs j
    left join companies c on j.company_name = c.company_name

),

scored as (

    select
        *,

        -- Freshness: a week-old posting is worth far more than a 60-day-old one.
        case
            when days_since_posted <= 7   then 40
            when days_since_posted <= 14  then 30
            when days_since_posted <= 30  then 20
            when days_since_posted <= 60  then 10
            else 0
        end
        +
        -- Seniority match for a student: internships first, entry next,
        -- senior roles heavily penalised since they are not realistically open.
        case seniority
            when 'intern' then 40
            when 'entry'  then 30
            when 'mid'    then 10
            else 0
        end
        +
        -- Companies hiring at pace are more likely to still be interviewing.
        least(coalesce(company_roles_per_week, 0) * 4, 20)::int
        as opportunity_score

    from joined

),

/*
  Collapse to one row per distinct role.

  A company posting the same job in twelve cities is one opportunity, not
  twelve. Keeping the posting grain here let BAE Systems occupy the entire
  top twelve with a single intern role. We keep the freshest posting and
  record how many locations it spans, which is itself a useful signal.
*/
roles as (

    select
        *,
        count(*)      over (partition by company_name, lower(job_title)) as locations_posted,
        row_number()  over (
            partition by company_name, lower(job_title)
            order by days_since_posted asc, job_id
        ) as _role_rn
    from scored

),

deduped_roles as (

    select * from roles where _role_rn = 1

)

select
    *,
    row_number() over (order by opportunity_score desc, days_since_posted asc) as priority_rank
from deduped_roles
