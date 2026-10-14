# Prove that Rails cannot write to the warehouse.
#
#     bin/rails runner script/verify_isolation.rb
#
# This is the Rails counterpart of service/sandbox.py's verify(), and it is
# deliberately the same kind of thing: it does not inspect configuration and
# conclude that writes are impossible, it ATTEMPTS the writes and fails if any of
# them succeeds. A configuration check tells you what someone intended; this tells
# you what the database will actually permit.
#
# The same file records why that distinction earned its keep - the demo role's
# sweep was written as a DELETE first, the database refused it, and the code was
# rewritten rather than the grant widened. A check that only read the config would
# have passed and the DELETE would have shipped.
#
# Exits non-zero on any failure, so CI and a pre-deploy step can both gate on it.
#
# There are POSITIVE controls too. A script that only proves writes fail would also
# pass if the credentials were wrong, the schema were missing, or the app could not
# reach the database at all - which is "safe" in the least useful sense. So reading
# the warehouse and writing to `tracker` must both work.

require "digest"

PASS = "\e[32m✓\e[0m"
FAIL = "\e[31m✗\e[0m"

@failures = []

# A forbidden action: it must raise, AND it must raise for the right reason.
#
# `expect` is a list of patterns one of which must match the error. Without it a
# typo in a table name would "pass" - the write would fail with UndefinedTable and
# the script would report the database as safely confined when it had tested
# nothing at all. A refusal for the wrong reason is not evidence.
def must_refuse(what, expect:)
  yield
  @failures << what
  puts "#{FAIL} #{what}"
  puts "      IT SUCCEEDED. This must not be possible."
rescue StandardError => e
  label = "#{e.class.name.split('::').last}: #{e.message.lines.first.to_s.strip}"
  if Array(expect).any? { |pattern| label.match?(pattern) }
    puts "#{PASS} #{what}"
    puts "      refused: #{label[0, 92]}"
  else
    @failures << "#{what} (refused, but for the WRONG reason)"
    puts "#{FAIL} #{what}"
    puts "      refused for the WRONG reason: #{label[0, 92]}"
    puts "      expected one of: #{Array(expect).map(&:inspect).join(', ')}"
  end
end

# The two refusals that count as real confinement.
READONLY  = /ReadOnlyRecord|ReadOnlyError/        # ActiveRecord refused
PRIVILEGE = /InsufficientPrivilege|permission denied/  # Postgres refused

# A required action: it must work.
def must_allow(what)
  result = yield
  puts "#{PASS} #{what}"
  result
rescue StandardError => e
  @failures << what
  puts "#{FAIL} #{what}"
  puts "      #{e.class.name}: #{e.message.lines.first.to_s.strip[0, 140]}"
  nil
end

puts
puts "Rails -> Signal warehouse isolation"
puts "environment: #{Rails.env}"
puts "=" * 72
puts
puts "-- Layer 0: the two connections must be genuinely separate sessions ---"

must_allow("primary and warehouse have DIFFERENT Postgres backends") do
  primary_pid   = ApplicationRecord.connection.select_value("SELECT pg_backend_pid()")
  warehouse_pid = WarehouseRecord.connection.select_value("SELECT pg_backend_pid()")

  if primary_pid == warehouse_pid
    raise <<~MSG.strip
      both connections share backend pid #{primary_pid}.
      This is Neon's PgBouncer in TRANSACTION mode multiplexing them onto one
      server backend. search_path is session state on that backend, so the two
      connections will silently swap schemas and the read-only split stops
      meaning anything. Point DATABASE_URL at the DIRECT endpoint - the host
      WITHOUT "-pooler" - and re-run.
    MSG
  end

  true
end

must_allow("search_paths are correct and do not bleed into each other") do
  checks = {
    "primary" => [ ApplicationRecord.connection.select_value("SHOW search_path"), "tracker" ],
    "warehouse" => [ WarehouseRecord.connection.select_value("SHOW search_path"), "public" ]
  }
  wrong = checks.reject { |_, (actual, expected)| actual.to_s.include?(expected) }
  unless wrong.empty?
    raise wrong.map { |name, (actual, expected)|
      "#{name} search_path is #{actual.inspect}, expected to contain #{expected.inspect}"
    }.join("; ")
  end

  # And they must still be right after a write on the primary, which is the exact
  # sequence that exposed the shared-backend problem.
  probe = JobApplication.create!(
    source: "__searchpath_probe__", job_id: "probe",
    company_name: "Probe", job_title: "Probe", saved_at: Time.current
  )
  probe.destroy!

  after = WarehouseRecord.connection.select_value("SHOW search_path")
  unless after.to_s.include?("public")
    raise "after a primary write the warehouse search_path became #{after.inspect}"
  end

  true
end

puts
puts "-- Positive controls: the app must actually work ----------------------"

posting = must_allow("read public.apply_queue (the posting list)") do
  Posting.take.tap { |p| raise "apply_queue returned no rows" if p.nil? }
end

must_allow("read public.outreach_tracker (the agent's drafts)") do
  OutreachDraft.count
end

must_allow("write to tracker.job_applications (Rails owns this)") do
  app = JobApplication.create!(
    source: "__isolation_probe__", job_id: "probe",
    company_name: "Probe", job_title: "Probe", saved_at: Time.current
  )
  app.destroy!
  true
end

puts
puts "-- Layer 1: ActiveRecord refuses writes on warehouse models -----------"

if posting
  must_refuse("Posting#update! (readonly? override)", expect: READONLY) do
    posting.update!(fit_score: 1)
  end
end

must_refuse("Posting.update_all (bypasses instance readonly?)", expect: READONLY) do
  Posting.where(source: "__nope__").update_all(fit_score: 1)
end

must_refuse("Posting.delete_all", expect: READONLY) do
  Posting.where(source: "__nope__").delete_all
end

must_refuse("OutreachDraft#update! (the agent owns this table)", expect: READONLY) do
  draft = OutreachDraft.take
  raise ActiveRecord::ReadOnlyRecord, "no drafts to probe with" if draft.nil?

  draft.update!(notes: "probe")
end

puts
puts "-- Layer 2: Postgres refuses, even with raw SQL -----------------------"
puts "   (these bypass ActiveRecord entirely and must still fail)"

warehouse = WarehouseRecord.connection

must_refuse("raw INSERT INTO public.outreach_tracker", expect: [ READONLY, PRIVILEGE ]) do
  warehouse.execute(<<~SQL)
    INSERT INTO public.outreach_tracker (source, job_id, status)
    VALUES ('__isolation_probe__', 'probe', 'not_contacted')
  SQL
end

must_refuse("raw UPDATE public.raw_postings", expect: [ READONLY, PRIVILEGE ]) do
  warehouse.execute("UPDATE public.raw_postings SET job_title = job_title WHERE false")
end

must_refuse("raw DELETE FROM public.apply_queue", expect: [ READONLY, PRIVILEGE ]) do
  warehouse.execute("DELETE FROM public.apply_queue WHERE false")
end

must_refuse("raw TRUNCATE public.posting_technologies", expect: [ READONLY, PRIVILEGE ]) do
  warehouse.execute("TRUNCATE public.posting_technologies")
end

must_refuse("DDL: CREATE TABLE in public", expect: [ READONLY, PRIVILEGE ]) do
  warehouse.execute("CREATE TABLE public.__isolation_probe__ (id int)")
end

must_refuse("DDL: ALTER a dbt mart (adding a column would vanish nightly anyway)", expect: [ READONLY, PRIVILEGE ]) do
  warehouse.execute("ALTER TABLE public.apply_queue ADD COLUMN __probe__ int")
end

must_refuse("read a public table Rails was never granted (job_enrichment)", expect: PRIVILEGE) do
  warehouse.execute("SELECT 1 FROM public.job_enrichment LIMIT 1")
end

puts
puts "-- Layer 3: migrations cannot target the warehouse --------------------"

must_allow("warehouse connection is configured database_tasks: false") do
  # include_hidden is required, and its necessity is itself the proof: Rails
  # treats a `database_tasks: false` config as hidden and omits it from the
  # default configs_for, which is exactly the mechanism that keeps migrations
  # and db:* tasks away from the warehouse.
  visible = ActiveRecord::Base.configurations
                              .configs_for(env_name: Rails.env).map(&:name)
  raise "warehouse is VISIBLE to database tasks: #{visible.inspect}" if visible.include?("warehouse")

  cfg = ActiveRecord::Base.configurations
                          .configs_for(env_name: Rails.env, name: "warehouse", include_hidden: true)
  raise "no warehouse configuration found" if cfg.nil?
  raise "database_tasks is not false - migrations COULD target the warehouse" if cfg.database_tasks?
  raise "replica is not true - ActiveRecord would permit writes" unless cfg.replica?

  true
end

must_allow("schema_migrations lives in tracker, not public") do
  schema = ActiveRecord::Base.connection.select_value(<<~SQL)
    SELECT schemaname FROM pg_tables WHERE tablename = 'schema_migrations'
  SQL
  raise "schema_migrations is in #{schema.inspect}, expected \"tracker\"" unless schema == "tracker"

  true
end

must_allow("no Rails table leaked into public") do
  leaked = ActiveRecord::Base.connection.select_values(<<~SQL)
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename IN ('schema_migrations','ar_internal_metadata','users','sessions',
                        'job_applications','job_application_notes','outreach_approvals')
  SQL
  raise "these Rails tables are in public: #{leaked.join(', ')}" if leaked.any?

  true
end

must_allow("no tracker table shadows a public name") do
  clashes = ActiveRecord::Base.connection.select_values(<<~SQL)
    SELECT t.tablename FROM pg_tables t
    WHERE t.schemaname = 'tracker'
      AND EXISTS (SELECT 1 FROM pg_tables p
                  WHERE p.schemaname = 'public' AND p.tablename = t.tablename)
  SQL
  raise "tracker tables shadowing public: #{clashes.join(', ')}" if clashes.any?

  true
end

puts
puts "=" * 72

if @failures.empty?
  puts "#{PASS} #{"All checks passed."} Rails can read the warehouse and write only to tracker."
  exit 0
else
  puts "#{FAIL} #{@failures.size} CHECK(S) FAILED:"
  @failures.each { |f| puts "    - #{f}" }
  puts
  puts "RAILS IS NOT CONFINED - do not deploy."
  exit 1
end
