{{ config(materialized='table') }}

/*
  Technology dimension, from the seeded controlled vocabulary.

  Conformed: both fct_job_posting (via posting_technologies) and
  fct_market_snapshot join to this, so "Snowflake" means the same entity
  whether you are counting job postings or market-wide demand.
*/

with tech as (

    select * from {{ ref('technologies') }}

),

usage as (

    select tech_slug, count(*) as postings_mentioning
    from {{ source('signal_market', 'posting_technologies') }}
    group by 1

)

select
    {{ dbt_utils.generate_surrogate_key(['t.tech_slug']) }} as technology_key,
    t.tech_slug,
    t.tech_name,
    t.category,
    t.is_tracked,
    coalesce(u.postings_mentioning, 0) as postings_mentioning,
    -- Ubiquitous technologies say little about an employer. Flagged here so
    -- the outreach layer and any ranking can exclude them consistently
    -- rather than each keeping its own list.
    t.tech_slug in ('python','sql','java','aws','azure','gcp','git','docker',
                    'machine_learning','bash','typescript') as is_ubiquitous
from tech t
left join usage u on u.tech_slug = t.tech_slug
