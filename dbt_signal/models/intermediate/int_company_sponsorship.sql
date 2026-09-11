{{ config(materialized='table') }}

/*
  Verified sponsorship history per company.

  Replaces the product's weakest signal. Eligibility verdicts were inferences
  from job text, and the text almost never says: under 4% of postings mention
  sponsorship at all, even at full length. These are filings an employer
  legally made, with real attested wages.

  Confidence is carried through rather than flattened. A name matched exactly
  is a fact; a single generic word matched to the start of a legal entity name
  might be a different organisation entirely ("Lighthouse" resolving to
  "LIGHTHOUSE BEHAVIORAL SOLUTIONS"), so it is marked and excluded from any
  claim that gets stated as fact.
*/

with links as (

    -- One row per (company, DOL entity). A company can own several, and
    -- picking one understates badly: Capital One files as CAPITAL ONE SERVICES
    -- (1,029 filings) and CAPITAL ONE NATIONAL ASSOCIATION (523), PwC across
    -- five entities totalling 1,778, Cognizant across four totalling 15,355.
    -- Only links the matcher or a human actually accepted appear here.
    select * from {{ source('signal_dol', 'company_employer_link') }}

),

filings as (

    select
        employer_key,
        sum(filings)                                          as total_filings,
        sum(certified)                                        as total_certified,
        sum(tech_filings)                                     as tech_filings,
        max(fiscal_year)                                      as latest_filing_year,
        count(distinct fiscal_year)                           as years_filing,
        -- Weighted by volume so a one-filing year cannot swing the figure.
        round(sum(median_wage * filings) / nullif(sum(filings), 0))
                                                              as weighted_median_wage,
        max(max_wage)                                         as highest_wage,
        min(rank_in_year)                                     as best_rank_in_year
    from {{ source('signal_dol', 'dol_employer_summary') }}
    group by 1

),

per_company as (

    -- Summed across the company's entities. The weighted median is recomputed
    -- from the totals rather than averaged, so a one-filing subsidiary cannot
    -- move a figure built from thousands.
    select
        l.company_name,
        min(l.employer_key)                          as employer_key,
        count(*)                                     as linked_entities,
        min(l.match_type)                            as match_type,
        -- Cast back to an integer: summing a bigint yields numeric, which
        -- reaches the browser search payload as a Decimal and is not JSON
        -- serialisable. ANSI cast() rather than ::, because these models are
        -- built on more than one engine.
        cast(sum(f.total_filings) as bigint)         as total_filings,
        cast(sum(f.total_certified) as bigint)       as total_certified,
        cast(sum(f.tech_filings) as bigint)          as tech_filings,
        max(f.latest_filing_year)                    as latest_filing_year,
        max(f.years_filing)                          as years_filing,
        round(sum(f.weighted_median_wage * f.total_filings)
              / nullif(sum(f.total_filings), 0))     as weighted_median_wage,
        max(f.highest_wage)                          as highest_wage,
        min(f.best_rank_in_year)                     as best_rank_in_year
    from links l
    join filings f on f.employer_key = l.employer_key
    group by 1

)

select
    company_name,
    employer_key,
    match_type,
    linked_entities,
    total_filings,
    total_certified,
    tech_filings,
    years_filing,
    latest_filing_year,
    weighted_median_wage,
    highest_wage,
    best_rank_in_year,
    round(100.0 * total_certified / nullif(total_filings, 0), 1) as certified_pct,

    -- Only these confidences may be presented as established fact. alias_seed
    -- is among them because a human wrote it, having checked the entity; it is
    -- the only non-mechanical source here and the most reliable.
    match_type in ('exact', 'alias_seed', 'prefix_strong') as is_confident_match,

    -- The product-facing verdict. Deliberately conservative: filing history
    -- proves an employer HAS sponsored, not that they will for a given role.
    case
        when match_type not in ('exact', 'alias_seed', 'prefix_strong')
            then 'unverified'
        when total_filings >= 20 then 'frequent_sponsor'
        when total_filings >= 3  then 'has_sponsored'
        else 'rarely_sponsors'
    end as sponsorship_status

from per_company
