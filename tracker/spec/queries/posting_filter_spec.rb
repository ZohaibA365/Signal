require "rails_helper"

# The filter is where the browse page's behaviour actually lives, and most of these
# rules come from what apply_queue really contains rather than from a preference.
RSpec.describe PostingFilter do
  before(:all) do
    Warehouse.reset!
    # A corpus shaped like the real one: mostly unscored, a few actionable, some
    # aggregator-only links, a mix of levels and sponsorship states.
    @intern_us = Warehouse.posting(company_name: "Capital One", job_title: "Data Scientist Intern",
                                   seniority: "intern", country: "us", fit_score: 88,
                                   recommendation: "apply now", posting_sponsorship: "offered_in_posting",
                                   location_state: "Virginia", days_since_posted: 2)
    @senior_ca = Warehouse.posting(company_name: "Shopify", job_title: "Senior Data Engineer",
                                   seniority: "senior", country: "ca", fit_score: 60,
                                   recommendation: "worth a look", posting_sponsorship: "has_sponsored",
                                   location_state: "Ontario", days_since_posted: 40)
    @blocked   = Warehouse.posting(company_name: "Defence Corp", job_title: "Data Engineer",
                                   seniority: "mid", country: "us", fit_score: 70,
                                   recommendation: "low priority", eligibility: "blocked",
                                   posting_sponsorship: "no_sponsorship_this_role",
                                   days_since_posted: 30)
    @unscored  = Warehouse.posting(company_name: "Quiet Startup", job_title: "Data Engineer",
                                   recommendation: "not yet scored", fit_score: nil,
                                   eligibility: nil, sponsorship_signal: nil,
                                   days_since_posted: 60)
    @aggregator = Warehouse.posting(company_name: "Via Aggregator", job_title: "Data Analyst",
                                    recommendation: "apply now", link_url: nil, link_tier: "aggregator",
                                    days_since_posted: 90)
  end

  after(:all) { Warehouse.reset! }

  def keys(filter) = filter.results.map(&:key)

  describe "the default view" do
    # 91% of apply_queue is "not yet scored", because enrichment is incremental and
    # costs money. Opening on everything would show mostly blanks.
    it "shows only scored, actionable postings" do
      expect(keys(described_class.new)).to include(@intern_us.key, @senior_ca.key, @blocked.key)
      expect(keys(described_class.new)).not_to include(@unscored.key)
    end

    # Aggregator links are country-gated and break for anyone outside the posting's
    # own country, so they are counted in the market index and never shown as
    # something to click - the same rule the public site follows.
    it "excludes postings with no direct link" do
      expect(keys(described_class.new)).not_to include(@aggregator.key)
    end

    it "includes the unscored only when asked, and says how many" do
      filter = described_class.new(include_unscored: "1")
      expect(keys(filter)).to include(@unscored.key)
      expect(filter.total).to be > described_class.new.total
    end
  end

  describe "search" do
    it "matches on company or title" do
      expect(keys(described_class.new(q: "Shopify"))).to eq([ @senior_ca.key ])
      expect(keys(described_class.new(q: "Analyst", include_unscored: "1"))).to be_empty # aggregator-only
    end

    # All-of, the same rule search.js uses on the public job board: every word typed
    # must appear in one field or the other.
    it "requires every word, across both fields" do
      expect(keys(described_class.new(q: "capital intern"))).to eq([ @intern_us.key ])
      expect(keys(described_class.new(q: "capital shopify"))).to be_empty
    end

    it "treats LIKE metacharacters as literal text" do
      expect { described_class.new(q: "100%_").results.to_a }.not_to raise_error
      expect(keys(described_class.new(q: "100%_"))).to be_empty
    end
  end

  describe "sponsorship, as the three questions people actually have" do
    it "offered: the posting itself says so" do
      expect(keys(described_class.new(sponsorship: "offered"))).to eq([ @intern_us.key ])
    end

    it "on_record: the employer has filings with the DOL" do
      expect(keys(described_class.new(sponsorship: "on_record"))).to include(@intern_us.key)
    end

    # The useful negative filter: hide the roles that rule you out. It needs BOTH
    # columns, because one is the posting's own words and the other is the verdict.
    it "not_refused: hides a refusal and hides a blocked role" do
      result = keys(described_class.new(sponsorship: "not_refused"))
      expect(result).to include(@intern_us.key, @senior_ca.key)
      expect(result).not_to include(@blocked.key)
    end
  end

  describe "filters and sorts" do
    it "filters by country, seniority, state, fit and freshness" do
      expect(keys(described_class.new(country: "ca"))).to eq([ @senior_ca.key ])
      expect(keys(described_class.new(seniority: "intern"))).to eq([ @intern_us.key ])
      expect(keys(described_class.new(state: "Ontario"))).to eq([ @senior_ca.key ])
      expect(keys(described_class.new(min_fit: "80"))).to eq([ @intern_us.key ])
      expect(keys(described_class.new(max_days: "7"))).to eq([ @intern_us.key ])
    end

    it "sorts by fit, newest and rank" do
      expect(keys(described_class.new(sort: "fit")).first).to eq(@intern_us.key)
      expect(keys(described_class.new(sort: "newest")).first).to eq(@intern_us.key)
      expect(described_class.new(sort: "rank").results.map(&:apply_rank))
        .to eq(described_class.new(sort: "rank").results.map(&:apply_rank).sort)
    end
  end

  describe "untrusted input" do
    it "drops values outside the allowed set instead of querying with them" do
      filter = described_class.new(country: "zz", seniority: "wizard",
                                   sort: "; DROP TABLE apply_queue", recommendation: "lovely",
                                   sponsorship: "please", min_fit: "abc", max_days: "NaN")

      expect(filter.to_params).to eq({})
      expect(filter.sort).to eq(described_class::DEFAULT_SORT)
      expect { filter.results.to_a }.not_to raise_error
    end

    it "clamps a hostile limit rather than letting it select everything" do
      expect(described_class.new(limit: "99999999").limit).to eq(described_class::MAX_LIMIT)
      expect(described_class.new(limit: "-5").limit).to eq(described_class::PAGE_SIZE)
    end

    it "clamps fit to a real percentage" do
      expect(described_class.new(min_fit: "9999").min_fit).to eq(100)
      expect(described_class.new(min_fit: "-10").min_fit).to eq(0)
    end
  end

  describe "URL state" do
    # The filtered view has to be a link you can bookmark or send yourself, and the
    # back button has to mean something.
    it "round-trips only the params that are set" do
      params = { q: "data", country: "us", seniority: "intern", sort: "fit" }
      expect(described_class.new(params).to_params).to eq(params)
    end

    it "omits defaults so URLs stay short" do
      expect(described_class.new(sort: "rank", limit: "50").to_params).to eq({})
    end
  end

  describe "paging" do
    it "reports more when the set is larger than the window" do
      filter = described_class.new(limit: "50")
      expect(filter.more?).to eq(filter.total > 50)
    end

    it "counts the whole filtered set, not the page" do
      filter = described_class.new(limit: "50")
      expect(filter.total).to eq(filter.scope.count)
    end
  end
end
