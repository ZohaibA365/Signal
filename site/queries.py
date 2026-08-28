"""
Every query the static site needs, run once per build.

Deliberately one module of named queries rather than SQL scattered through
templates: the site makes public claims, so each figure has to be traceable to
a statement someone can re-run. Nothing here computes - the warehouse models
already did that. This only reads.

The build fetches everything up front and renders ~500 pages from memory. A
query per page would turn a 3-second build into minutes and hammer the
warehouse for no reason.
"""

from __future__ import annotations

# --- homepage ---------------------------------------------------------------

CORPUS_STATS = """
SELECT
    (SELECT count(*) FROM raw_postings)                                  AS postings,
    (SELECT count(*) FROM dim_company)                                   AS companies,
    (SELECT count(*) FROM market_demand
      WHERE snapshot_date = (SELECT max(snapshot_date) FROM market_demand)) AS technologies,
    (SELECT count(*) FROM int_company_sponsorship WHERE is_confident_match) AS verified_sponsors,
    (SELECT sum(total_filings) FROM int_company_sponsorship)             AS total_filings,
    (SELECT count(*) FROM posting_technologies)                          AS tech_mentions
"""

# Demand by category, for the league tables. Market-wide index counts - the
# trustworthy series, independent of which postings were collected.
DEMAND_BY_CATEGORY = """
SELECT category, tech_slug, tech_name, openings, pct_of_category, category_rank
FROM market_demand
WHERE snapshot_date = (SELECT max(snapshot_date) FROM market_demand)
ORDER BY category, openings DESC
"""

# Share of postings in the open-ended top salary band. Percentiles are useless
# here: the source histogram has seven buckets and its highest absorbs 62% of
# postings, so medians collapse onto one figure for nearly every technology.
SALARY_LEADERS = """
SELECT s.tech_slug, m.tech_name, s.category, s.pct_top_band, s.total_postings
FROM salary_by_tech s
JOIN market_demand m
  ON m.tech_slug = s.tech_slug AND m.snapshot_date = s.snapshot_date
WHERE s.sample_is_usable
  AND s.snapshot_date = (SELECT max(snapshot_date) FROM salary_by_tech)
ORDER BY s.pct_top_band DESC
"""

STACK_PAIRS = """
SELECT c.tech_slug, a.tech_name AS tech_name, c.co_tech_slug,
       b.tech_name AS co_tech_name, c.pct_of_tech_postings, c.lift, c.co_postings
FROM tech_cooccurrence c
JOIN dim_technology a ON a.tech_slug = c.tech_slug
JOIN dim_technology b ON b.tech_slug = c.co_tech_slug
WHERE c.tech_total >= 30 AND c.lift > 1
ORDER BY c.lift DESC
"""

# --- company pages ----------------------------------------------------------

COMPANIES = """
SELECT
    c.company_name, c.total_postings, c.postings_last_30d, c.postings_prior_30d,
    c.pace_change_pct, c.distinct_states, c.distinct_countries,
    c.distinct_departments, c.intern_postings, c.latest_posting,
    s.sponsorship_status, s.total_filings, s.certified_pct,
    s.weighted_median_wage, s.years_filing, s.match_type
FROM dim_company c
LEFT JOIN int_company_sponsorship s
       ON s.company_name = c.company_name AND s.is_confident_match
WHERE c.total_postings >= 5
ORDER BY c.total_postings DESC
"""

COMPANY_TECH = """
SELECT r.company_name, t.tech_slug, t.tech_name, t.category,
       count(DISTINCT r.job_id) AS mentions
FROM raw_postings r
JOIN posting_technologies pt ON pt.source = r.source AND pt.job_id = r.job_id
JOIN dim_technology t ON t.tech_slug = pt.tech_slug
WHERE r.company_name IN (
    SELECT company_name FROM dim_company WHERE total_postings >= 5
)
GROUP BY 1, 2, 3, 4
"""

COMPANY_ROLES = """
SELECT company_name, job_title, location_state, country, seniority,
       days_since_posted, redirect_url
FROM ranked_opportunities
WHERE company_name IN (
    SELECT company_name FROM dim_company WHERE total_postings >= 5
)
  AND days_since_posted <= 60
ORDER BY company_name, days_since_posted
"""

# --- technology pages -------------------------------------------------------

# Every technology in the vocabulary gets a page, not only the 46 snapshotted
# daily. Co-occurrence spans all 120, so restricting pages to the tracked
# subset left 867 links pointing at pages that were never generated. The
# untracked ones still carry real corpus data - mentions and co-occurring
# tools - they simply have no market-wide openings count.
TECH_DETAIL = """
SELECT t.tech_slug, t.tech_name, t.category, t.postings_mentioning,
       t.is_tracked, t.is_ubiquitous,
       m.openings, m.overall_rank, m.category_rank, m.pct_of_category,
       s.pct_top_band, s.pct_under_80k, s.total_postings AS salary_sample
FROM dim_technology t
LEFT JOIN market_demand m
       ON m.tech_slug = t.tech_slug
      AND m.snapshot_date = (SELECT max(snapshot_date) FROM market_demand)
LEFT JOIN salary_by_tech s
       ON s.tech_slug = t.tech_slug AND s.snapshot_date = m.snapshot_date
ORDER BY coalesce(m.openings, 0) DESC, t.postings_mentioning DESC
"""

TECH_EMPLOYERS = """
SELECT tech_slug, company_name, postings, rank
FROM market_snapshot_companies
WHERE snapshot_date = (SELECT max(snapshot_date) FROM market_snapshot_companies)
  AND rank <= 8
ORDER BY tech_slug, rank
"""

# --- search payload ---------------------------------------------------------

# Only the fields the browser filters or displays. Descriptions are excluded
# deliberately: including them would take the payload from ~250 kB gzipped to
# several megabytes and the browser never searches them.
SEARCH_ROWS = """
SELECT
    q.job_id, q.company_name, q.job_title, q.location_state, q.country,
    q.seniority, q.days_since_posted, q.salary_min_reported, q.redirect_url,
    q.fit_score, q.eligibility, q.sponsorship_status, q.sponsor_filings,
    string_agg(DISTINCT pt.tech_slug, ',') AS techs
FROM apply_queue q
LEFT JOIN posting_technologies pt
       ON pt.source = q.source AND pt.job_id = q.job_id
WHERE q.days_since_posted <= 60
GROUP BY q.job_id, q.company_name, q.job_title, q.location_state, q.country,
         q.seniority, q.days_since_posted, q.salary_min_reported, q.redirect_url,
         q.fit_score, q.eligibility, q.sponsorship_status, q.sponsor_filings
ORDER BY q.days_since_posted
"""

FRESHNESS = """
SELECT
    (SELECT max(snapshot_date) FROM market_demand)     AS latest_snapshot,
    (SELECT max(posted_date)::date FROM raw_postings)  AS latest_posting,
    (SELECT count(DISTINCT snapshot_date) FROM market_snapshots) AS days_of_history
"""
