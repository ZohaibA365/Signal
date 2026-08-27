{{ config(materialized='table') }}

/*
  Which technologies appear together in the same posting.

  This is what surfaces the actual stack clusters - whether Snowflake postings
  also ask for dbt, whether Airflow travels with Spark - rather than treating
  each tool as independent. It is also the basis for the "modern stack vs
  legacy stack" contrast on the public page, and for telling a company which
  tools their postings mention that their peers' do not.

  Emitted in both directions (a->b and b->a) so a lookup by either tool works
  without a union at query time.
*/

with pairs as (

    select
        a.tech_slug as tech_slug,
        b.tech_slug as co_tech_slug,
        count(*)    as co_postings
    from {{ source('signal_market', 'posting_technologies') }} a
    join {{ source('signal_market', 'posting_technologies') }} b
      on a.source = b.source
     and a.job_id = b.job_id
     and a.tech_slug <> b.tech_slug
    group by 1, 2

),

totals as (

    select tech_slug, count(*) as tech_postings
    from {{ source('signal_market', 'posting_technologies') }}
    group by 1

)

select
    p.tech_slug,
    p.co_tech_slug,
    p.co_postings,
    ta.tech_postings                                                  as tech_total,
    tb.tech_postings                                                  as co_tech_total,
    -- P(co_tech | tech): of postings naming this tool, how many also name the other.
    round(100.0 * p.co_postings / nullif(ta.tech_postings, 0), 1)     as pct_of_tech_postings,
    -- Lift > 1 means the pair appears together more than independence predicts.
    round((p.co_postings::numeric / nullif(ta.tech_postings, 0))
          / nullif(tb.tech_postings::numeric
                   / (select count(distinct source || job_id)
                      from {{ source('signal_market', 'posting_technologies') }}), 0), 2) as lift
from pairs p
join totals ta on ta.tech_slug = p.tech_slug
join totals tb on tb.tech_slug = p.co_tech_slug
where p.co_postings >= 3
