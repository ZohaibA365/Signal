{{ config(materialized='table') }}

/*
  Technology mentions over time, reconstructed from posting dates.

  The daily snapshot only starts producing trend after a week or two. This
  model gives a trend immediately by bucketing the postings already collected
  by their own posted_date, which spans roughly 12 months.

  IMPORTANT CAVEAT - this is a sample, not the market, and it is biased.
  Postings are collected today, so an old posting only appears here if it is
  STILL listed. Roles that filled quickly have already disappeared, which means
  older months over-represent slow-to-fill and evergreen postings. Recent
  months are far more reliable than distant ones.

  The public page must present this as "mentions among tracked postings" and
  never as market-wide demand. market_demand (from the daily snapshot) is the
  unbiased series; this one is the head start.
*/

with mentions as (

    select
        date_trunc('month', r.posted_date)::date as month,
        pt.tech_slug,
        count(distinct r.job_id)                 as postings
    from {{ source('signal_market', 'posting_technologies') }} pt
    join {{ source('signal', 'raw_postings') }} r
      on r.source = pt.source and r.job_id = pt.job_id
    where r.posted_date is not null
    group by 1, 2

),

monthly_totals as (

    select
        date_trunc('month', posted_date)::date as month,
        count(*)                               as total_postings
    from {{ source('signal', 'raw_postings') }}
    where posted_date is not null
    group by 1

)

select
    m.month,
    m.tech_slug,
    m.postings,
    t.total_postings,
    -- Share matters more than the raw count, because how many postings we
    -- collected in a month is an artefact of our sampling, not of the market.
    round(100.0 * m.postings / nullif(t.total_postings, 0), 2) as pct_of_postings,
    -- Threshold raised from 50 to 500 after inspecting real data. Months
    -- older than ~5 months hold 45-241 postings, and at that size a single
    -- posting swings a technology's share by a full percentage point. The
    -- thin months produced a fake "Python demand fell 70%" signal that was
    -- pure sampling noise compounded by survivorship bias.
    t.total_postings >= 500                                    as sample_is_usable
from mentions m
join monthly_totals t on t.month = m.month
order by m.month desc, m.postings desc
