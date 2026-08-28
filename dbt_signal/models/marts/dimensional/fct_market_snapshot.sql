{{
  config(
    materialized='incremental',
    unique_key='snapshot_key',
    incremental_strategy='delete+insert'
  )
}}

/*
  Market demand fact. Grain: one row per (snapshot_date, technology).

  The compounding asset in star form - the series that exists nowhere else
  because the source API only reports today. Conformed to the same
  dim_technology and dim_date as fct_job_posting, so one query can ask "how
  much does this company hire for X, against how much the market wants X".

  Genuinely append-only: yesterday's demand never changes. Rebuilding the
  whole table every run to add one day's 46 rows is pure waste, and grows
  linearly with the asset. The ranking windows are computed per snapshot_date
  so they stay correct when only one day is processed.
*/

with snapshots as (

    select * from {{ source('signal_market', 'market_snapshots') }}

    {% if is_incremental() %}
      where snapshot_date > (select coalesce(max(snapshot_date), '1900-01-01'::date) from {{ this }})
    {% endif %}

)

select
    {{ dbt_utils.generate_surrogate_key(['snapshot_date', 'tech_slug']) }} as snapshot_key,
    {{ dbt_utils.generate_surrogate_key(['tech_slug']) }}                  as technology_key,
    {{ dbt_utils.generate_surrogate_key(['snapshot_date']) }}              as snapshot_date_key,

    snapshot_date,
    tech_slug,

    -- Measures. Partitioned by snapshot_date, so a single-day incremental run
    -- produces the same ranks a full refresh would.
    openings,
    rank() over (partition by snapshot_date order by openings desc)  as rank_overall,
    rank() over (partition by snapshot_date, category
                 order by openings desc)                             as rank_in_category,
    round(100.0 * openings
          / nullif(sum(openings) over (partition by snapshot_date, category), 0), 2)
                                                                     as pct_of_category
from snapshots
