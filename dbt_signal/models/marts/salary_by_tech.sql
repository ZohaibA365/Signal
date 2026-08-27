{{ config(materialized='table') }}

/*
  Salary distribution per technology, from Adzuna's histogram endpoint.

  The histogram is market-wide and independent of which postings we happened
  to collect, which makes it far more trustworthy than the per-posting salary
  fields - ~99% of those are Adzuna's model estimates rather than figures the
  employer published.

  Buckets are lower bounds: bucket 140000 counts postings paying 140k up to
  the next bucket. The median here is a bucket, not a precise figure, and the
  public page should say so.
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

weighted as (

    select
        *,
        sum(posting_count) over (partition by snapshot_date, tech_slug) as total_postings,
        sum(posting_count) over (
            partition by snapshot_date, tech_slug
            order by salary_bucket
            rows between unbounded preceding and current row
        ) as running_count
    from buckets

),

percentiles as (

    select
        snapshot_date,
        tech_slug,
        tech_name,
        category,
        total_postings,
        -- Lowest bucket whose cumulative share crosses each threshold.
        min(salary_bucket) filter (where running_count >= 0.25 * total_postings) as p25_bucket,
        min(salary_bucket) filter (where running_count >= 0.50 * total_postings) as median_bucket,
        min(salary_bucket) filter (where running_count >= 0.75 * total_postings) as p75_bucket,
        round(sum(salary_bucket::numeric * posting_count)
              / nullif(sum(posting_count), 0))                                   as weighted_mean
    from weighted
    group by 1, 2, 3, 4, 5

)

select
    *,
    -- A thin sample makes percentiles meaningless; flag rather than hide.
    total_postings >= 100 as sample_is_usable
from percentiles
