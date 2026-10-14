# Turns request params into a Posting scope, and nothing else.
#
# A query object rather than scopes-in-the-controller for one practical reason: the
# browse page, the "show more" link and any future export all have to apply exactly
# the same rules, and the rules are the part worth getting right. The controller
# renders; this decides.
#
# Everything is driven off the real contents of apply_queue, measured 2026-10-14
# rather than assumed:
#
#   country             us 64,985  ·  ca 5,785
#   seniority           senior 35,816  ·  mid 27,422  ·  intern 5,243  ·  entry 2,289
#   recommendation      not yet scored 64,346  ·  skip 5,672  ·  low priority 603
#                       worth a look 91  ·  apply now 58
#   eligibility         NULL 67,922  ·  blocked 2,088  ·  unclear 413  ·  eligible 347
#   posting_sponsorship frequent_sponsor 27,385  ·  has_sponsored 7,739
#                       no_sponsorship_this_role 3,813  ·  rarely_sponsors 2,009
#                       offered_in_posting 1,326
#   link_tier           direct 60,112  ·  aggregator 10,658
#
# Two of those shape the defaults. `eligibility` and `sponsorship_signal` are NULL
# on the 67,922 rows the LLM has not scored, so a filter on either silently means
# "and also, only scored rows" - which is why the unscored set is opt-in and said
# out loud in the UI rather than quietly excluded. And `link_tier` splits exactly
# with link_url being present, so "direct" and "clickable" are the same filter;
# aggregator links are country-gated and break for anyone outside the posting's own
# country, which is why they are excluded by default.
class PostingFilter
  SORTS = {
    "rank" => { label: "Pipeline rank", order: { apply_rank: :asc } },
    "fit" => { label: "Best fit", order: { fit_score: :desc, apply_rank: :asc } },
    "newest" => { label: "Newest", order: { days_since_posted: :asc, apply_rank: :asc } },
    "salary" => { label: "Highest stated salary", order: { salary_min_reported: :desc } }
  }.freeze

  DEFAULT_SORT  = "rank"
  DEFAULT_LIMIT = 50
  PAGE_SIZE     = 50
  MAX_LIMIT     = 1_000

  SPONSORSHIP = {
    "offered" => "Sponsorship offered in the posting",
    "on_record" => "Employer has filed with the DOL",
    "not_refused" => "Has not refused sponsorship"
  }.freeze

  ATTRIBUTES = %i[q country seniority recommendation sponsorship state
                  min_fit max_days include_unscored sort limit].freeze

  attr_reader(*ATTRIBUTES)

  def initialize(params = {})
    p = params || {}
    @q               = p[:q].to_s.strip.presence
    @country         = presence_in(p[:country], %w[us ca])
    @seniority       = presence_in(p[:seniority], %w[intern entry mid senior])
    @recommendation  = presence_in(p[:recommendation], Posting::RECOMMENDATIONS)
    @sponsorship     = presence_in(p[:sponsorship], SPONSORSHIP.keys)
    @state           = p[:state].to_s.strip.presence
    @min_fit         = clamp_int(p[:min_fit], 0, 100)
    @max_days        = clamp_int(p[:max_days], 0, 3_650)
    @include_unscored = ActiveModel::Type::Boolean.new.cast(p[:include_unscored]) || false
    @sort            = presence_in(p[:sort], SORTS.keys) || DEFAULT_SORT
    @limit           = clamp_int(p[:limit], PAGE_SIZE, MAX_LIMIT) || DEFAULT_LIMIT
  end

  # The filtered set, unordered and unlimited. `results` and `total` both build on
  # this so a count can never disagree with the rows shown.
  def scope
    s = Posting.all
    s = s.clickable unless include_unscored && recommendation == "not yet scored"
    s = include_unscored ? s : s.where(recommendation: Posting::ACTIONABLE)

    s = apply_search(s)
    s = s.where(country: country)                  if country
    s = s.where(seniority: seniority)              if seniority
    s = s.where(recommendation: recommendation)     if recommendation
    s = s.where(location_state: state)             if state
    s = s.where(fit_score: min_fit..)              if min_fit
    s = s.where(days_since_posted: ..max_days)     if max_days
    s = apply_sponsorship(s)
    s
  end

  def results
    scope.order(SORTS.fetch(sort)[:order]).limit(limit)
  end

  def total
    @total ||= scope.count
  end

  def more?
    total > limit
  end

  def next_limit
    [ limit + PAGE_SIZE, MAX_LIMIT ].min
  end

  # Only the params that are actually set, so URLs stay short and shareable and the
  # back button means something.
  def to_params
    {
      q: q, country: country, seniority: seniority, recommendation: recommendation,
      sponsorship: sponsorship, state: state, min_fit: min_fit, max_days: max_days,
      include_unscored: (include_unscored ? "1" : nil),
      sort: (sort unless sort == DEFAULT_SORT),
      limit: (limit unless limit == DEFAULT_LIMIT)
    }.compact
  end

  def filtered?
    to_params.except(:sort, :limit).any?
  end

  private

  # All-of substring matching across title and company, the same rule the public
  # job board's search.js uses: every word typed must appear in one field or the
  # other. "capital data" finds "Data Scientist" at "Capital One".
  #
  # ILIKE rather than a tsvector index because apply_queue has no indexes at all
  # and Rails may not add one - dbt drops and recreates the table nightly, taking
  # any index with it.
  def apply_search(scope)
    return scope if q.blank?

    q.split(/\s+/).reduce(scope) do |acc, word|
      pattern = "%#{sanitize_like(word)}%"
      acc.where("job_title ILIKE :p OR company_name ILIKE :p", p: pattern)
    end
  end

  # Three questions people actually have, rather than one dropdown per column.
  def apply_sponsorship(scope)
    case sponsorship
    when "offered"
      # The posting itself says so. The strongest signal there is.
      scope.where(posting_sponsorship: "offered_in_posting")
    when "on_record"
      # The employer has told the US government what it intended to pay a foreign
      # worker. A record of having sponsored, not a promise about this role.
      scope.where(sponsor_filings: 1..)
    when "not_refused"
      # The useful negative filter: hide roles that rule you out. Both columns
      # matter - one is the posting's own words, the other the eligibility verdict.
      scope.where.not(posting_sponsorship: "no_sponsorship_this_role")
           .where("eligibility IS DISTINCT FROM 'blocked'")
    else
      scope
    end
  end

  def presence_in(value, allowed)
    v = value.to_s.strip
    allowed.include?(v) ? v : nil
  end

  def clamp_int(value, min, max)
    return nil if value.blank?

    Integer(value, exception: false)&.clamp(min, max)
  end

  def sanitize_like(string)
    ActiveRecord::Base.sanitize_sql_like(string)
  end
end
