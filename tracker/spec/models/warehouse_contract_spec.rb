require "rails_helper"

# Two things this file protects.
#
# The read-only guarantee, asserted here as well as in script/verify_isolation.rb
# so a regression fails in CI rather than only when someone remembers to run the
# script by hand.
#
# And the stand-in schema's fidelity. spec/support/warehouse_schema.sql is a
# hand-written copy of two warehouse relations, so it can drift from the real ones.
# The specific drift that would hurt is the app reading a column the stand-in does
# not have: the specs would pass and production would fail.
RSpec.describe "the warehouse contract" do
  # Committed before any example transaction, for the reason given in
  # spec/support/warehouse.rb: the writer and the reader are separate sessions.
  before(:all) do
    Warehouse.reset!
    @posting_key = Warehouse.posting.key
    @draft_key = Warehouse.draft.key
  end

  after(:all) { Warehouse.reset! }

  describe "read-only enforcement" do
    it "refuses to update a Posting" do
      posting = Posting.find_by_key(@posting_key)
      expect { posting.update!(fit_score: 1) }.to raise_error(ActiveRecord::ReadOnlyRecord)
    end

    # ReadOnlyError, not ReadOnlyRecord: `replica: true` intercepts the query before
    # WarehouseRecord's own update_all override is reached. The override is kept as
    # the layer that would still refuse if the replica flag were ever removed, so
    # this asserts either is acceptable rather than pinning the current order.
    it "refuses the class-level writers that bypass readonly?" do
      [ -> { Posting.where(source: "x").update_all(fit_score: 1) },
        -> { Posting.where(source: "x").delete_all } ].each do |write|
        expect(&write).to raise_error { |e|
          expect(e).to be_a(ActiveRecord::ReadOnlyError).or be_a(ActiveRecord::ReadOnlyRecord)
        }
      end
    end

    it "refuses to update an OutreachDraft" do
      expect { OutreachDraft.find_by_key(@draft_key).update!(notes: "x") }
        .to raise_error(ActiveRecord::ReadOnlyRecord)
    end

    it "keeps the warehouse connection out of database tasks" do
      visible = ActiveRecord::Base.configurations.configs_for(env_name: "test").map(&:name)
      expect(visible).not_to include("warehouse"),
                             "database_tasks: false is what stops a migration targeting the warehouse"
    end

    it "puts Rails' own tables in the tracker schema, not public" do
      leaked = ActiveRecord::Base.connection.select_values(<<~SQL)
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN ('schema_migrations','ar_internal_metadata','users','sessions',
                            'job_applications','job_application_notes','outreach_approvals')
      SQL
      expect(leaked).to be_empty
    end

    # search_path shadowing works in both directions: a tracker table reusing a
    # public name would silently resolve to the wrong one.
    it "has no tracker table shadowing a public name" do
      clashes = ActiveRecord::Base.connection.select_values(<<~SQL)
        SELECT t.tablename FROM pg_tables t
        WHERE t.schemaname = 'tracker'
          AND EXISTS (SELECT 1 FROM pg_tables p
                      WHERE p.schemaname = 'public' AND p.tablename = t.tablename)
      SQL
      expect(clashes).to be_empty
    end
  end

  describe "the stand-in schema covers what the app reads" do
    # Every column named anywhere in the app code for these two models. Collected by
    # hand rather than by reflection, because the point is to catch a column the
    # stand-in is MISSING, and reflection would only ever report what it has.
    POSTING_COLUMNS = %w[
      source job_id country company_name job_title location_state posted_date
      days_since_posted seniority salary_min_reported link_url link_tier
      eligibility sponsorship_signal fit_score fit_reasoning sponsor_filings
      posting_sponsorship recommendation apply_rank
    ].freeze

    DRAFT_COLUMNS = %w[
      source job_id company_name job_title status draft_email draft_connection
      draft_followup draft_source notes updated_at
    ].freeze

    it "has every apply_queue column the app reads" do
      expect(Posting.column_names).to include(*POSTING_COLUMNS)
    end

    it "has every outreach_tracker column the app reads" do
      expect(OutreachDraft.column_names).to include(*DRAFT_COLUMNS)
    end

    # The real outreach_tracker is declared in storage/schema.sql, one directory up.
    # If a column is added there and the stand-in does not gain it, this says so.
    it "matches storage/schema.sql's outreach_tracker declaration" do
      schema_sql = Rails.root.join("../storage/schema.sql")
      skip "storage/schema.sql not found" unless schema_sql.exist?

      ddl = schema_sql.read[/CREATE TABLE IF NOT EXISTS outreach_tracker\s*\((.*?)\n\);/m, 1]
      skip "outreach_tracker declaration not found" if ddl.nil?

      declared = ddl.lines.filter_map { |l| l.strip[/\A([a-z_]+)\s+[A-Z]/, 1] }
      expect(OutreachDraft.column_names).to include(*declared)
    end

    # apply_queue has no primary key in the real warehouse, and Posting declares the
    # composite one at the model level. A stand-in WITH a key would make that work
    # for the wrong reason.
    it "leaves apply_queue without a database primary key, as dbt does" do
      pk = ActiveRecord::Base.connection.select_value(<<~SQL)
        SELECT count(*) FROM pg_constraint
        WHERE conrelid = 'public.apply_queue'::regclass AND contype = 'p'
      SQL
      expect(pk.to_i).to eq(0)
      expect(Posting.primary_key).to eq([ "source", "job_id" ])
    end
  end
end
