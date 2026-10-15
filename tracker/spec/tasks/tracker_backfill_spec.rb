require "rails_helper"
require "rake"

RSpec.describe "tracker:backfill" do
  before(:all) do
    Warehouse.reset!
    3.times { |i| Warehouse.posting(recommendation: "apply now", company_name: "Acme #{i}") }
    Warehouse.posting(recommendation: "worth a look", company_name: "Maybe Co")
    @aggregator_job_id = Warehouse.posting(recommendation: "apply now", company_name: "Via Aggregator",
                                           link_url: nil, link_tier: "aggregator").job_id

    Rake::Task.clear
    Tracker::Application.load_tasks
  end

  # The task saves into the owner's account. Created per example so the
  # transaction rolls it back; the warehouse rows above are committed and shared.
  let!(:owner) do
    User.create!(email_address: "owner@example.com", password: "owner-password-12", demo: false)
  end

  after(:all) { Warehouse.reset! }

  def run(env = {})
    env.each { |k, v| ENV[k.to_s] = v.to_s }
    Rake::Task["tracker:backfill"].reenable
    original = $stdout
    $stdout = StringIO.new
    Rake::Task["tracker:backfill"].invoke
    $stdout.string
  ensure
    $stdout = original
    env.each_key { |k| ENV.delete(k.to_s) }
  end

  it "saves the recommended, directly linkable postings" do
    expect { run }.to change(JobApplication, :count).by(3)
    expect(JobApplication.pluck(:status).uniq).to eq([ "saved" ])
  end

  # An aggregator link is country-gated and breaks outside the posting's own
  # country, so it is never something to click - the same rule the browse page
  # and the public site follow.
  it "skips postings with no direct link" do
    run
    expect(JobApplication.pluck(:job_id)).not_to include(@aggregator_job_id)
  end

  it "takes only the recommendation asked for" do
    run
    expect(JobApplication.pluck(:company_name)).not_to include("Maybe Co")
  end

  # The reason this is a task and not part of db:seed: seed runs on every deploy,
  # and re-adding a role you had removed would overwrite a decision.
  it "is idempotent, so running it twice adds nothing" do
    run
    expect { run }.not_to change(JobApplication, :count)
  end

  it "writes nothing on a dry run" do
    expect { run(DRY_RUN: "1") }.not_to change(JobApplication, :count)
  end

  it "copies the fields that must outlive the posting" do
    run
    app = JobApplication.first
    expect(app.company_name).to be_present
    expect(app.job_title).to be_present
    expect(app.link_url).to be_present
  end

  it "refuses a recommendation that is not one" do
    expect { run(RECOMMENDATION: "brilliant") }.to raise_error(SystemExit)
  end
end
