# One job posting, with everything the pipeline has concluded about it.
#
# Reads `public.apply_queue`, a dbt mart
# (dbt_signal/models/marts/apply_queue.sql, materialized='table'). It joins
# ranked_opportunities, job_enrichment and the DOL sponsorship tables, so a single
# row carries the posting, the LLM's eligibility and fit judgements, and the
# employer's verified visa filing history.
#
# Two facts about this table shape the code below.
#
# It has NO primary key and no indexes at all. Identity is (source, job_id) by
# convention, declared on the model rather than in the database. A full-table sort
# or an unscoped search sequentially scans 32 MB / ~70,000 rows, which is why
# `default_browse` exists and why the controller uses it.
#
# 91% of rows are `not yet scored`, because enrichment is incremental and costs
# money. Measured 2026-10-14: 70,770 rows, of which 2,848 have a fit score and
# only 752 carry an actionable recommendation. A UI that opens on everything shows
# mostly blanks, so `default_browse` is the default view and the unscored rows are
# behind a toggle.
class Posting < WarehouseRecord
  self.table_name = "apply_queue"

  # Composite key, Rails 7.1+ idiom. A model-level declaration, which is what lets
  # it work over a table that has no database primary key. These records are
  # read-only so the key is only ever used for lookup, never for an UPDATE's
  # WHERE clause.
  self.primary_key = [ :source, :job_id ]

  # The vocabulary dbt guarantees. Asserted by a dbt test in
  # dbt_signal/models/marts/_market_models.yml:100-124, so these are the only
  # values that can appear - but they are listed here rather than imported,
  # because a dbt change should break a test here rather than silently change a
  # filter's meaning.
  RECOMMENDATIONS = [ "apply now", "worth a look", "low priority", "skip", "not yet scored" ].freeze
  ACTIONABLE      = [ "apply now", "worth a look", "low priority" ].freeze

  # What the browse page opens on: rows the pipeline has actually judged.
  scope :default_browse, -> { where(recommendation: ACTIONABLE) }

  scope :scored, -> { where.not(fit_score: nil) }

  # Only postings whose link was verified to reach the employer are worth showing.
  # Everything else still counts in the market index, where no link is involved -
  # the same rule the public site follows.
  scope :clickable, -> { where.not(link_url: nil) }

  # A stable identifier for URLs and for joining to Rails-owned tables. The site
  # and the agent both use this "source:job_id" shape - service/app.py:223 reads
  # `source || ':' || job_id` - so it is worth matching exactly.
  def key
    "#{source}:#{job_id}"
  end

  def self.find_by_key(key)
    source, job_id = key.to_s.split(":", 2)
    return nil if source.blank? || job_id.blank?

    find_by(source: source, job_id: job_id)
  end

  def scored?
    fit_score.present?
  end

  # True when the employer has filings on record with the Department of Labor.
  # Absence is weak evidence, not a no: employers file under legal entity names
  # that do not always resolve to the name on a posting.
  def sponsor_on_record?
    sponsor_filings.to_i.positive?
  end
end
