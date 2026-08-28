{{ config(materialized='table') }}

/*
  Date spine covering every date the warehouse references.

  Built from observed min/max rather than a fixed range, so it cannot silently
  fail to cover a backfill. Gives the facts a conformed date key and makes
  month/quarter grouping a join instead of a repeated date_trunc.
*/

with bounds as (

    select
        least(
            coalesce((select min(posted_date)::date from {{ ref('stg_jobs') }}), current_date),
            coalesce((select min(snapshot_date) from {{ source('signal_market', 'market_snapshots') }}), current_date)
        ) as start_date,
        greatest(
            coalesce((select max(posted_date)::date from {{ ref('stg_jobs') }}), current_date),
            current_date
        ) + 1 as end_date

),

spine as (

    select generate_series(start_date, end_date, interval '1 day')::date as date_day
    from bounds

)

select
    {{ dbt_utils.generate_surrogate_key(['date_day']) }} as date_key,
    date_day,
    extract(year   from date_day)::int  as year,
    extract(quarter from date_day)::int as quarter,
    extract(month  from date_day)::int  as month,
    to_char(date_day, 'YYYY-MM')        as year_month,
    to_char(date_day, 'Mon YYYY')       as month_label,
    extract(dow    from date_day)::int  as day_of_week,
    extract(dow from date_day) in (0, 6) as is_weekend,
    date_day > current_date              as is_future
from spine
