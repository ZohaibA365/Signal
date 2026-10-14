require "rails_helper"

RSpec.describe "The tracker" do
  before(:all) do
    Warehouse.reset!
    @posting_key = Warehouse.posting(company_name: "Acme Data",
                                     job_title: "Data Engineer Intern").key
  end

  after(:all) { Warehouse.reset! }

  let(:posting) { Posting.find_by_key(@posting_key) }

  before { sign_in }

  it "saves a posting, copying what must outlive it" do
    expect { post job_applications_path, params: { posting_key: posting.key } }
      .to change(JobApplication, :count).by(1)

    app = JobApplication.last
    expect(app.status).to eq("saved")
    expect(app.company_name).to eq("Acme Data")
    expect(app.link_url).to eq(posting.link_url)
  end

  it "is idempotent: saving twice does not duplicate" do
    post job_applications_path, params: { posting_key: posting.key }
    expect { post job_applications_path, params: { posting_key: posting.key } }
      .not_to change(JobApplication, :count)
  end

  it "moves a role through the pipeline and stamps the date" do
    post job_applications_path, params: { posting_key: posting.key }
    app = JobApplication.last

    patch job_application_path(app), params: { job_application: { status: "applied" } }
    expect(app.reload.status).to eq("applied")
    expect(app.applied_at).to be_present
  end

  it "refuses a status outside the vocabulary" do
    post job_applications_path, params: { posting_key: posting.key }
    app = JobApplication.last

    patch job_application_path(app), params: { job_application: { status: "hired" } }
    expect(app.reload.status).to eq("saved")
    expect(flash[:alert]).to be_present
  end

  it "keeps notes against a role, newest visible, and removes them" do
    post job_applications_path, params: { posting_key: posting.key }
    app = JobApplication.last

    expect { post job_application_notes_path(app), params: { job_application_note: { body: "Phone screen Thursday" } } }
      .to change(app.notes, :count).by(1)

    get job_applications_path
    expect(response.body).to include("Phone screen Thursday")

    note = app.notes.last
    expect { delete job_application_note_path(app, note) }.to change(app.notes, :count).by(-1)
  end

  it "rejects an empty note" do
    post job_applications_path, params: { posting_key: posting.key }
    app = JobApplication.last
    expect { post job_application_notes_path(app), params: { job_application_note: { body: "  " } } }
      .not_to change(app.notes, :count)
  end

  it "removes a role and its notes together" do
    post job_applications_path, params: { posting_key: posting.key }
    app = JobApplication.last
    post job_application_notes_path(app), params: { job_application_note: { body: "x" } }

    expect { delete job_application_path(app) }.to change(JobApplication, :count).by(-1)
    expect(JobApplicationNote.count).to eq(0)
  end

  it "refuses to save a posting that is not in the warehouse" do
    expect { post job_applications_path, params: { posting_key: "company_board:nope:1" } }
      .not_to change(JobApplication, :count)
    expect(flash[:alert]).to be_present
  end
end
