{{ config(materialized='table') }}

/*
  Market demand fact. Grain: one row per (snapshot_date, technology).

  This is the compounding asset in star form - the series that exists nowhere
  else because the source API only reports today. Conformed to the same
  dim_technology and dim_date as fct_job_posting, so a single query can ask
  "how much does this company hire for X, against how much the market wants X".
  That question is the whole point of the outreach engine's market tier.
*/

with snapshots as (

    select * from {{ source('signal_market', 'market_snapshots') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['s.snapshot_date', 's.tech_slug']) }} as snapshot_key,
    {{ dbt_utils.generate_surrogate_key(['s.tech_slug']) }}                    as technology_key,
    {{ dbt_utils.generate_surrogate_key(['s.snapshot_date']) }}                as snapshot_date_key,

    s.snapshot_date,
    s.tech_slug,

    -- Measures
    s.openings,
    rank() over (partition by s.snapshot_date order by s.openings desc)   as rank_overall,
    rank() over (partition by s.snapshot_date, s.category
                 order by s.openings desc)                                as rank_in_category,
    round(100.0 * s.openings
          / nullif(sum(s.openings) over (partition by s.snapshot_date, s.category), 0), 2)
                                                                          as pct_of_category
from snapshots s
