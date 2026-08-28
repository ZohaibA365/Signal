{{ config(materialized='table') }}

/*
  What each technology pays, from the job board's market-wide salary histogram.

  This histogram is independent of which postings Signal happened to collect,
  which makes it far more trustworthy than the per-posting salary fields -
  ~99% of those are model estimates rather than employer-published figures.

  THE HISTOGRAM IS COARSE AND TOP-CODED. There are seven buckets and the
  highest ($140k) is open-ended: it absorbs every posting paying 140k or more.
  For Python that single bucket holds 21,858 of 35,411 postings. Percentiles
  are therefore near-useless - p25, median and p75 all landed on $140,000 for
  most technologies, which says nothing about which pays better.

  So the headline metric here is pct_top_band: the share of postings in the
  open-ended top band. That discriminates cleanly (DuckDB 95%, ClickHouse 93%
  against a mid-70s field) and is honest about what the data can support.

  weighted_mean is kept but is a LOWER BOUND, because it counts every
  top-band posting as exactly $140k when the real figure is higher.
*/

with buckets as (

    select
        s.snapshot_date,
        s.tech_slug,
        m.tech_name,
        m.category,
        s.salary_bucket,
        s.posting_count
    from {{ source('signal_market', 'market_snapshot_salary') }} s
    join {{ source('signal_market', 'market_snapshots') }} m
      on m.snapshot_date = s.snapshot_date and m.tech_slug = s.tech_slug

),

top_band as (

    select snapshot_date, max(salary_bucket) as top_bucket
    from buckets
    group by 1

),

aggregated as (

    select
        b.snapshot_date,
        b.tech_slug,
        b.tech_name,
        b.category,
        sum(b.posting_count)                                        as total_postings,

        -- Headline: share of postings in the open-ended top band.
        round(100.0 * sum(case when b.salary_bucket >= t.top_bucket
                                then b.posting_count else 0 end)
              / nullif(sum(b.posting_count), 0), 1)                 as pct_top_band,

        -- Lower bound only - see the note above.
        round(sum(b.salary_bucket::numeric * b.posting_count)
              / nullif(sum(b.posting_count), 0))                    as weighted_mean_floor,

        -- Share below $80k, the other end that the buckets can actually resolve.
        round(100.0 * sum(case when b.salary_bucket < 80000 then b.posting_count else 0 end)
              / nullif(sum(b.posting_count), 0), 1)                 as pct_under_80k,

        max(t.top_bucket)                                           as top_bucket
    from buckets b
    join top_band t on t.snapshot_date = b.snapshot_date
    group by 1, 2, 3, 4

)

select
    *,
    total_postings >= 150 as sample_is_usable
from aggregated
