{{ config(materialized='table') }}

/*
  Current technology demand, with movement over time.

  One row per technology per snapshot date. The delta columns are null until
  enough days have accumulated - that is expected, not a bug. This table is
  the reason the snapshot job runs daily: the level is mildly interesting, the
  trend is the product.
*/

with snapshots as (

    select
        snapshot_date,
        tech_slug,
        tech_name,
        category,
        openings
    from {{ source('signal_market', 'market_snapshots') }}

),

with_history as (

    select
        *,
        -- Compare against the reading closest to N days back rather than
        -- N rows back, so a missed run does not silently shift the window.
        lag(openings) over w                          as prev_openings,
        first_value(openings) over w_7                as openings_7d_ago,
        first_value(openings) over w_30               as openings_30d_ago,
        count(*) over (partition by tech_slug)        as observations
    from snapshots
    window
        w    as (partition by tech_slug order by snapshot_date),
        w_7  as (partition by tech_slug order by snapshot_date
                 range between interval '7 days' preceding and current row),
        w_30 as (partition by tech_slug order by snapshot_date
                 range between interval '30 days' preceding and current row)

),

ranked as (

    select
        *,
        rank() over (partition by snapshot_date order by openings desc) as overall_rank,
        rank() over (partition by snapshot_date, category
                     order by openings desc)                            as category_rank,
        round(100.0 * openings
              / nullif(sum(openings) over (partition by snapshot_date, category), 0), 1)
                                                                        as pct_of_category
    from with_history

)

select
    snapshot_date,
    tech_slug,
    tech_name,
    category,
    openings,
    overall_rank,
    category_rank,
    pct_of_category,

    openings - prev_openings                                        as change_since_last,
    case when openings_7d_ago  > 0 and observations > 1
         then round(100.0 * (openings - openings_7d_ago)  / openings_7d_ago,  1) end
                                                                    as pct_change_7d,
    case when openings_30d_ago > 0 and observations > 1
         then round(100.0 * (openings - openings_30d_ago) / openings_30d_ago, 1) end
                                                                    as pct_change_30d,

    observations,
    -- Honest labelling for the public page: a trend claim needs history behind it.
    observations >= 7 as has_trend

from ranked
