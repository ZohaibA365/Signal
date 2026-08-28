{{ config(materialized='table') }}

/*
  Company dimension.

  Exists to stop company attributes being repeated on every posting row -
  Databricks alone appears on 499 of them. It is also the grain the outreach
  engine works at, and the grain at which eligibility should be judged: a
  defence contractor's citizenship requirement is a fact about the employer,
  not about each individual req.
*/

with postings as (

    select * from {{ ref('stg_jobs') }}

),

aggregated as (

    select
        company_name,
        count(*)                                                as total_postings,
        count(*) filter (where posted_date > current_date - 30)  as postings_last_30d,
        count(*) filter (where posted_date > current_date - 60
                           and posted_date <= current_date - 30) as postings_prior_30d,
        count(distinct location_state) filter (where location_state is not null)
                                                                as distinct_states,
        count(distinct country)                                 as distinct_countries,
        count(distinct category) filter (where category is not null)
                                                                as distinct_departments,
        count(*) filter (where seniority = 'intern')            as intern_postings,
        min(posted_date)::date                                  as first_seen_posting,
        max(posted_date)::date                                  as latest_posting,
        count(distinct source)                                  as source_count
    from postings
    group by 1

)

select
    {{ dbt_utils.generate_surrogate_key(['company_name']) }} as company_key,
    company_name,
    total_postings,
    postings_last_30d,
    postings_prior_30d,
    distinct_states,
    distinct_countries,
    distinct_departments,
    intern_postings,
    first_seen_posting,
    latest_posting,
    source_count,
    -- Hiring momentum, null when the prior window is too thin to divide by.
    case
        when postings_prior_30d >= 3
        then round(100.0 * (postings_last_30d - postings_prior_30d)
                   / postings_prior_30d)
    end as pace_change_pct,
    total_postings >= 8 as has_enough_data
from aggregated
