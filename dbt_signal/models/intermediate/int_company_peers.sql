{{ config(materialized='table') }}

/*
  Data-derived peer companies.

  Peers are the companies hiring for the most similar technology mix, measured
  by Jaccard similarity over each company's set of mentioned technologies. No
  model and no hand-maintained competitor list: the comparison has to be
  reproducible because it gets published on a company's own page and sent to
  people who work there.

  THE FLOOR IS THE WHOLE POINT. Similarity alone is not enough - a company
  with three extracted technologies can score 0.43 against something unrelated.
  Inspecting the best peer across a spread of companies showed a clean break:

      Snowflake  -> Databricks        43 shared   0.63
      Instacart  -> Stripe            35 shared   0.61
      Datadog    -> Scale AI          31 shared   0.61
      Ramp       -> Brex              19 shared   0.54
      ---------------------------------------------- floor
      J.P.Morgan -> NTT America        7 shared   0.64
      Google     -> CACI International 4 shared   0.44
      IBM        -> Citigroup          3 shared   0.43

  Everything above 19 shared technologies is defensible; everything at or
  below 7 is noise that happens to score well. The floor sits at 15.

  The bias this introduces is worth stating plainly: companies whose postings
  come from their own career boards carry full descriptions and therefore rich
  technology extraction, while aggregator-sourced companies are truncated at
  500 characters and extract far less. So peers resolve well for the former and
  are suppressed for the latter. Suppression is the correct outcome - a page
  asserting a wrong peer, read by someone who works there, is worse than a
  page that stays quiet.
*/

{% set min_shared = 15 %}
{% set min_similarity = 0.35 %}
{% set min_postings = 15 %}

with company_tech as (

    select distinct
        r.company_name,
        pt.tech_slug
    from {{ source('signal', 'raw_postings') }} r
    join {{ source('signal_market', 'posting_technologies') }} pt
      on pt.source = r.source and pt.job_id = r.job_id
    join {{ ref('dim_company') }} d
      on d.company_name = r.company_name
     and d.total_postings >= {{ min_postings }}

),

totals as (

    select company_name, count(*) as tech_count
    from company_tech
    group by 1

),

pairs as (

    select
        a.company_name                                   as company_name,
        b.company_name                                   as peer_name,
        count(*)                                         as shared_technologies,
        round(count(*)::numeric
              / (ta.tech_count + tb.tech_count - count(*)), 3) as similarity
    from company_tech a
    join company_tech b
      on a.tech_slug = b.tech_slug
     and a.company_name <> b.company_name
    join totals ta on ta.company_name = a.company_name
    join totals tb on tb.company_name = b.company_name
    group by 1, 2, ta.tech_count, tb.tech_count

),

ranked as (

    select
        *,
        row_number() over (
            partition by company_name
            order by similarity desc, shared_technologies desc
        ) as peer_rank
    from pairs
    where shared_technologies >= {{ min_shared }}
      and similarity          >= {{ min_similarity }}

)

select
    company_name,
    peer_name,
    peer_rank,
    shared_technologies,
    similarity
from ranked
where peer_rank <= 6
