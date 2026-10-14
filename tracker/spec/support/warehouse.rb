# Seeding the warehouse stand-ins.
#
# WHY THIS COMMITS, and why warehouse rows are seeded once per suite rather than
# per example.
#
# The app holds TWO connections to the same database - `primary` on search_path
# `tracker` and `warehouse` on `public` - and they are separate Postgres sessions.
# Transactional tests wrap each example in a transaction on each connection, so a
# row INSERTed through `primary` inside that transaction is invisible to
# `warehouse`, which is a different session reading committed data only. A spec
# that seeded a posting per example would then find no postings.
#
# So warehouse rows are written in before(:suite), before any example transaction
# opens, and committed. They are read-only to the app anyway, which is the whole
# premise, so there is nothing for an example to change about them.
#
# The INSERTs go through `primary` with explicit `public.` qualification, because
# the `warehouse` connection is configured `replica: true` and refuses writes - the
# behaviour verify_isolation.rb exists to prove.
module Warehouse
  SCHEMA = Rails.root.join("spec/support/warehouse_schema.sql")

  class << self
    def load_schema!
      refuse_unless_disposable!
      connection.execute(File.read(SCHEMA))
    end

    # warehouse_schema.sql contains `DROP TABLE public.apply_queue`. Pointed at the
    # real warehouse that would destroy a dbt mart and, with it, the job board -
    # recoverable only by a full dbt run, and only if someone noticed.
    #
    # So this refuses to run anywhere that is not obviously disposable. It is the
    # single most destructive line in this repository and the only one guarded by
    # name: a test suite should not be able to drop a production table because
    # someone exported the wrong DATABASE_URL.
    def refuse_unless_disposable!
      db = connection.current_database
      host = connection.pool.db_config.configuration_hash[:host].to_s
      url = connection.pool.db_config.configuration_hash[:url].to_s

      looks_local = host.empty? || host.match?(/\Alocalhost\z|\A127\.0\.0\.1\z|\Apostgres\z/) ||
                    url.match?(%r{//[^/@]*@?(localhost|127\.0\.0\.1|postgres)[:/]})
      named_test = db.to_s.end_with?("_test")

      return if Rails.env.test? && named_test && looks_local

      raise <<~MSG
        REFUSING to load the warehouse stand-in schema.

        It runs DROP TABLE public.apply_queue, which would destroy a dbt mart if
        this is not a disposable database.

          Rails.env : #{Rails.env}       (must be test)
          database  : #{db}              (must end in _test)
          host      : #{host.presence || '(from url)'}  (must be local)

        If you meant to run the specs, point DATABASE_URL at a local throwaway
        Postgres - see docker-compose.yml - not at Neon.
      MSG
    end

    def reset!
      refuse_unless_disposable!
      connection.execute("TRUNCATE public.apply_queue, public.outreach_tracker")
    end

    # A posting, with sensible defaults so a spec states only what it is about.
    def posting(source: "company_board", job_id: nil, **overrides)
      job_id ||= "job-#{SecureRandom.hex(4)}"
      row = {
        source: source, job_id: job_id,
        country: "us", company_name: "Acme Data", job_title: "Data Engineer",
        location_state: "New York", days_since_posted: 3, seniority: "intern",
        link_url: "https://acme.example/jobs/#{job_id}", link_tier: "direct",
        fit_score: 80, fit_reasoning: "Matches the profile.",
        recommendation: "apply now", apply_rank: next_rank,
        posting_sponsorship: "frequent_sponsor", sponsor_filings: 42,
        eligibility: "eligible", sponsorship_signal: "unclear",
        salary_min_reported: nil
      }.merge(overrides)

      insert(:apply_queue, row)
      Posting.find_by(source: row[:source], job_id: row[:job_id])
    end

    # A draft the agent would have written.
    def draft(source: "company_board", job_id: nil, **overrides)
      job_id ||= "draft-#{SecureRandom.hex(4)}"
      row = {
        source: source, job_id: job_id,
        company_name: "Acme Data", job_title: "Data Engineer",
        status: "email_drafted",
        draft_email: "Subject: Acme Data's hiring\n\nHi,\n\nOne true thing.\n\nAlex",
        draft_connection: "Short note.", draft_followup: "Longer note.",
        draft_source: "model"
      }.merge(overrides)

      insert(:outreach_tracker, row)
      OutreachDraft.find_by(source: row[:source], job_id: row[:job_id])
    end

    # Change a draft's text the way the agent's upsert would, and COMMIT it.
    #
    # This opens its OWN connection rather than using the pool, because inside an
    # example every pooled connection is wrapped in a transaction that will be
    # rolled back - and the `warehouse` session reads committed data only, so it
    # would go on seeing the old text and an approval would never look stale. The
    # test would then pass for the wrong reason, or fail for a reason that has
    # nothing to do with the code.
    def redraft!(draft, text)
      refuse_unless_disposable!
      raw = PG::Connection.new(**pg_params)
      begin
        raw.exec_params(
          "UPDATE public.outreach_tracker SET draft_email = $1 WHERE source = $2 AND job_id = $3",
          [ text, draft.source, draft.job_id ]
        )
      ensure
        raw.close
      end
      OutreachDraft.find_by(source: draft.source, job_id: draft.job_id)
    end

    private

    # `primary`: it is not a replica, so it may write, and `public.` qualification
    # means the search_path (`tracker`) does not matter.
    def connection
      ActiveRecord::Base.connection
    end

    def insert(table, row)
      columns = row.keys.map { |k| %("#{k}") }.join(", ")
      placeholders = row.each_key.with_index(1).map { |_, i| "$#{i}" }.join(", ")
      connection.exec_insert(
        "INSERT INTO public.#{table} (#{columns}) VALUES (#{placeholders})",
        "seed", row.values
      )
    end

    def next_rank
      @rank = (@rank || 0) + 1
    end

    # Connection parameters for a one-off raw connection, taken from whatever the
    # test environment is actually configured with.
    def pg_params
      cfg = ActiveRecord::Base.connection_pool.db_config.configuration_hash
      if cfg[:url].present?
        uri = URI.parse(cfg[:url])
        { host: uri.host, port: uri.port, dbname: uri.path.delete_prefix("/"),
          user: uri.user, password: uri.password }.compact
      else
        { host: cfg[:host], port: cfg[:port], dbname: cfg[:database],
          user: cfg[:username], password: cfg[:password] }.compact
      end
    end
  end
end
