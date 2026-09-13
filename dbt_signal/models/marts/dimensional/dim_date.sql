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
    -- 'yyyy-MM', not 'YYYY-MM'. Postgres and Snowflake accept either and mean the
    -- same thing; Spark rejects the upper-case form outright, because YYYY there
    -- is the week-based year and it refuses the ambiguity. Measured on all three:
    -- 'yyyy-MM' returns 2011-06 everywhere.
    to_char(cast(date_day as date), 'yyyy-MM')  as year_month,

    -- Spelled out rather than formatted, because no format string means the same
    -- thing on all three. 'Mon YYYY' gives "Jun 2011" on Postgres and Snowflake
    -- and is rejected by Spark; 'MMM yyyy' gives "Jun 2011" on Spark and
    -- "06M 2011" on the other two, since MMM there is the month number followed
    -- by a literal M. A CASE has no dialect.
    case extract(month from date_day)
        when 1 then 'Jan' when 2  then 'Feb' when 3  then 'Mar'
        when 4 then 'Apr' when 5  then 'May' when 6  then 'Jun'
        when 7 then 'Jul' when 8  then 'Aug' when 9  then 'Sep'
        when 10 then 'Oct' when 11 then 'Nov' when 12 then 'Dec'
    end || ' ' || to_char(cast(date_day as date), 'yyyy') as month_label,
    extract(dow from date_day)            as day_of_week,
    extract(dow from date_day) in (0, 6)  as is_weekend,
    cast(date_day as date) > current_date as is_future
from spine
