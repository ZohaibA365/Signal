{{ config(materialized='table') }}

/*
  Date spine giving the facts a conformed date key, and making month or
  quarter grouping a join rather than a repeated date_trunc.

  PORTABILITY: uses dbt_utils.date_spine rather than generate_series, which is
  Postgres-only. The bounds are fixed rather than derived from the data,
  because a cross-database spine macro cannot take a subquery - so the range is
  set wide enough to cover the full posting history (earliest observed posting
  is 2019) and several years ahead.
*/

with spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2018-01-01' as date)",
        end_date="cast('2030-01-01' as date)"
    ) }}

)

select
    {{ dbt_utils.generate_surrogate_key(['date_day']) }} as date_key,
    cast(date_day as date)                as date_day,
    extract(year    from date_day)        as year,
    extract(quarter from date_day)        as quarter,
    extract(month   from date_day)        as month,
    to_char(cast(date_day as date), 'YYYY-MM')  as year_month,
    to_char(cast(date_day as date), 'Mon YYYY') as month_label,
    extract(dow from date_day)            as day_of_week,
    extract(dow from date_day) in (0, 6)  as is_weekend,
    cast(date_day as date) > current_date as is_future
from spine
