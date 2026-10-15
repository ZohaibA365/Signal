# Two accounts.
#
# The owner's, from the environment so no password ever enters git, and a sample
# account whose credentials are printed on the sign-in page. The sample account
# is read-only (ApplicationController refuses any non-GET from it) and sees only
# the rows seeded below - never the owner's applications, notes or drafts.
#
#     bin/rails db:seed
#
# Re-running is safe and is how you rotate a password or refresh the samples.

# --- the owner ---------------------------------------------------------------
email = ENV["ADMIN_EMAIL"]
password = ENV["ADMIN_PASSWORD"]

if email.blank? || password.blank?
  abort "db:seed needs ADMIN_EMAIL and ADMIN_PASSWORD. Nothing was created."
end
abort "ADMIN_PASSWORD must be at least 12 characters." if password.length < 12

owner = User.find_or_initialize_by(email_address: email)
created = owner.new_record?
owner.password = password
owner.demo = false
owner.save!
owner.sessions.destroy_all unless created
puts created ? "created owner #{owner.email_address}" : "updated owner #{owner.email_address}"

# --- the sample account ------------------------------------------------------
DEMO_EMAIL = "sample@signal.app".freeze
DEMO_PASSWORD = ENV.fetch("DEMO_PASSWORD", "sample-account").freeze

demo = User.find_or_initialize_by(email_address: DEMO_EMAIL)
demo.password = DEMO_PASSWORD
demo.demo = true
demo.save!

# Rebuilt every run, so the sample data is always the same whatever anyone
# clicked. Only the demo account's rows are touched; `dependent: :destroy` on
# the association means this cannot reach the owner's.
demo.job_applications.destroy_all
demo.outreach_approvals.destroy_all
DemoDraft.delete_all

# Invented companies. Real ones would imply this person actually applied there.
SAMPLE_ROLES = [
  { company: "Northwind Analytics", title: "Data Engineer, New Grad",      status: "interviewing", days: 21,
    note: "Second round scheduled. They use dbt and Airflow, same stack as here." },
  { company: "Pelham Robotics",     title: "Data Platform Intern",          status: "applied",      days: 9,
    note: "Applied through their own board. Recruiter replied same day." },
  { company: "Haversack Logistics", title: "Analytics Engineer (Co-op)",    status: "offer",        days: 34,
    note: "Verbal offer. Deciding by Friday." },
  { company: "Calder & Finch",      title: "Junior Data Scientist",         status: "rejected",     days: 40,
    note: "No sponsorship after all - worth checking earlier next time." },
  { company: "Orbit Grocery",       title: "Data Engineering Intern",       status: "saved",        days: 3,
    note: nil },
  { company: "Tessellate Health",   title: "Machine Learning Engineer I",   status: "saved",        days: 5,
    note: "Asks for Spark, which I have." }
].freeze

SAMPLE_ROLES.each_with_index do |r, i|
  app = demo.job_applications.create!(
    source: "sample", job_id: "role-#{i + 1}",
    company_name: r[:company], job_title: r[:title],
    link_url: "https://example.com/jobs/#{i + 1}",
    status: r[:status], saved_at: r[:days].days.ago
  )
  app.advance_to!(r[:status]) unless r[:status] == "saved"
  app.notes.create!(body: r[:note]) if r[:note]
end

SAMPLE_DRAFTS = [
  { company: "Northwind Analytics", title: "Data Engineer, New Grad", source_kind: "model",
    body: <<~TEXT },
      Subject: Dagster in Northwind Analytics' postings

      Northwind Analytics mentions Dagster across 4 postings, which almost no
      other company in this dataset does.

      I'm a statistics student looking for a summer placement. Rather than a
      resume, I'd value your opinion: does that match how the stack actually
      looks from inside?

      Sam Whitfield
    TEXT
  { company: "Pelham Robotics", title: "Data Platform Intern", source_kind: "template",
    body: <<~TEXT },
      Subject: Pelham Robotics' hiring, from the data side

      Hi,

      I was looking through a public dataset on data-engineering hiring, and
      Pelham Robotics stood out: they are one of the few companies in this
      dataset that mention Apache Iceberg in job postings at all.

      I'm a statistics student looking for a summer placement. Rather than a
      resume, I'd value your opinion: does this match how hiring actually looks
      from inside Pelham Robotics?

      Sam Whitfield
    TEXT
  { company: "Tessellate Health", title: "Machine Learning Engineer I", source_kind: "model",
    body: <<~TEXT }
      Subject: Tessellate Health and MLflow

      Tessellate Health names MLflow in 3 of its open roles - unusual for a
      company of its size in this dataset.

      I'm a statistics student looking for a summer placement. Rather than a
      resume, I'd value your opinion: does that reflect how the team actually
      works?

      Sam Whitfield
    TEXT
].freeze

SAMPLE_DRAFTS.each_with_index do |d, i|
  DemoDraft.create!(
    source: "sample", job_id: "draft-#{i + 1}",
    company_name: d[:company], job_title: d[:title],
    status: "email_drafted", draft_source: d[:source_kind],
    draft_email: d[:body].strip,
    draft_connection: "Short note for a connection request.",
    draft_followup: "Longer follow-up once the request is accepted."
  )
end

puts "sample account #{demo.email_address}: " \
     "#{demo.job_applications.count} roles, #{DemoDraft.count} drafts"
puts "owner #{owner.email_address}: #{owner.job_applications.count} roles"
