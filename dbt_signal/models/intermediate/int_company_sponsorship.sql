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

with mapping as (

    select * from {{ source('signal_dol', 'company_employer_key') }}
    where employer_key is not null

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

)

select
    m.company_name,
    m.employer_key,
    m.match_type,
    f.total_filings,
    f.total_certified,
    f.tech_filings,
    f.years_filing,
    f.latest_filing_year,
    f.weighted_median_wage,
    f.highest_wage,
    f.best_rank_in_year,
    round(100.0 * f.total_certified / nullif(f.total_filings, 0), 1) as certified_pct,

    -- Only these two confidences may be presented as established fact.
    m.match_type in ('exact', 'prefix_strong') as is_confident_match,

    -- The product-facing verdict. Deliberately conservative: filing history
    -- proves an employer HAS sponsored, not that they will for a given role.
    case
        when m.match_type not in ('exact', 'prefix_strong') then 'unverified'
        when f.total_filings >= 20 then 'frequent_sponsor'
        when f.total_filings >= 3  then 'has_sponsored'
        else 'rarely_sponsors'
    end as sponsorship_status

from mapping m
join filings f on f.employer_key = m.employer_key
