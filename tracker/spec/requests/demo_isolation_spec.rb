require "rails_helper"

# The sample account must not be able to see or change the owner's data.
#
# This is the spec that makes a public login defensible. Everything else in the
# suite checks that a feature works; these check that a feature does NOT work
# for the wrong person, which is the only kind of guarantee worth printing
# credentials on a sign-in page for.
#
# Both halves are tested: what the sample account cannot READ, and what it
# cannot WRITE. Either one failing alone would be enough to expose real data.
# The fixture names are deliberately ugly single tokens. ERB escapes an
# apostrophe to &#39;, so a fixture called "Owner's Secret Employer" never
# appears verbatim in the rendered page - and every `not_to include(...)` below
# would have passed whether or not the data leaked. A string that survives
# escaping unchanged is the only kind these assertions can be built on.
RSpec.describe "Sample-account isolation" do
  before(:all) do
    Warehouse.reset!
    @posting_key = Warehouse.posting(company_name: "Acme Data").key
    @real_draft_key = Warehouse.draft(company_name: "A Real Company",
                                      draft_email: "Subject: real and private\n\nOWNERDRAFTWORDS.").key
  end

  after(:all) { Warehouse.reset! }

  let(:owner) do
    User.create!(email_address: "owner@example.com", password: "owner-password-12", demo: false)
  end

  let(:demo) do
    User.create!(email_address: "sample@example.com", password: "sample-password-12", demo: true)
  end

  # Something private belonging to the owner, for the sample account to fail to reach.
  let!(:owner_application) do
    owner.job_applications.create!(source: "company_board", job_id: "private-1",
                                   company_name: "OWNERSECRETEMPLOYER",
                                   job_title: "OWNERPRIVATEROLE")
  end

  let!(:owner_note) { owner_application.notes.create!(body: "OWNERPRIVATENOTE") }

  let!(:owner_approval) do
    OutreachApproval.create!(user: owner, source: "company_board", job_id: "private-1",
                             decision: "approved", approved_text: "OWNERAPPROVEDTEXT",
                             draft_fingerprint: OutreachApproval.fingerprint_of("OWNERAPPROVEDTEXT"))
  end

  let!(:sample_draft) do
    DemoDraft.create!(source: "sample", job_id: "d1", company_name: "Northwind Analytics",
                      job_title: "Data Engineer", draft_email: "Subject: sample\n\nSample words.",
                      draft_source: "model")
  end

  def sign_in_as(user, password)
    post session_path, params: { email_address: user.email_address, password: password }
    follow_redirect! if response.redirect?
  end

  describe "what the sample account cannot see" do
    before { sign_in_as(demo, "sample-password-12") }

    it "does not show the owner's tracked roles" do
      get job_applications_path
      expect(response.body).not_to include("OWNERSECRETEMPLOYER")
      expect(response.body).not_to include("OWNERPRIVATEROLE")
    end

    it "does not show the owner's notes" do
      get job_applications_path
      expect(response.body).not_to include("OWNERPRIVATENOTE")
    end

    # The sharpest one. public.outreach_tracker holds real unsent emails written
    # about real companies; the sample account reads DemoDraft instead.
    it "does not show real agent drafts, only the seeded samples" do
      get outreach_approvals_path
      expect(response.body).not_to include("A Real Company")
      expect(response.body).not_to include("OWNERDRAFTWORDS")
      expect(response.body).to include("Northwind Analytics")
    end

    it "does not show the owner's approval decisions" do
      get outreach_approvals_path
      expect(response.body).not_to include("OWNERAPPROVEDTEXT")
    end

    it "shows its own sample roles instead" do
      demo.job_applications.create!(source: "sample", job_id: "s1",
                                    company_name: "Pelham Robotics", job_title: "Intern")
      get job_applications_path
      expect(response.body).to include("Pelham Robotics")
    end

    # Counts are rendered from a separate query, and an unscoped one there would
    # leak the shape of the owner's pipeline even while hiding the rows.
    it "counts only its own roles" do
      demo.job_applications.create!(source: "sample", job_id: "c1",
                                    company_name: "Orbit", job_title: "Intern")
      get job_applications_path

      # The counts partial always renders every status label, so the label is no
      # evidence. The number beside it is: the owner has a saved role too, and
      # the sample account must count 1, not 2.
      saved = response.body[/Saved<\/span>\s*(\d+)/, 1]
      expect(saved).to eq(demo.job_applications.where(status: "saved").count.to_s)
    end

    # A guessed id must 404 rather than load someone else's row.
    it "cannot reach the owner's row by id" do
      expect { get job_applications_path }.not_to raise_error
      patch job_application_path(owner_application),
            params: { job_application: { status: "offer" } }
      expect(owner_application.reload.status).not_to eq("offer")
    end
  end

  describe "what the sample account cannot change" do
    before { sign_in_as(demo, "sample-password-12") }

    it "cannot save a posting" do
      expect { post job_applications_path, params: { posting_key: @posting_key } }
        .not_to change(JobApplication, :count)
    end

    it "cannot change a status" do
      app = demo.job_applications.create!(source: "sample", job_id: "s2",
                                          company_name: "Orbit", job_title: "Intern")
      patch job_application_path(app), params: { job_application: { status: "applied" } }
      expect(app.reload.status).to eq("saved")
    end

    it "cannot add a note" do
      app = demo.job_applications.create!(source: "sample", job_id: "s3",
                                          company_name: "Orbit", job_title: "Intern")
      expect { post job_application_notes_path(app), params: { job_application_note: { body: "x" } } }
        .not_to change(JobApplicationNote, :count)
    end

    it "cannot delete anything" do
      app = demo.job_applications.create!(source: "sample", job_id: "s4",
                                          company_name: "Orbit", job_title: "Intern")
      expect { delete job_application_path(app) }.not_to change(JobApplication, :count)
    end

    it "cannot approve a draft" do
      expect {
        post outreach_approvals_path, params: { draft_key: sample_draft.key, decision: "approved",
                                                approved_text: sample_draft.draft_email }
      }.not_to change(OutreachApproval, :count)
    end

    # The refusal is in ApplicationController, before any action runs, so it
    # cannot be defeated by a request the views never generate.
    it "refuses every non-GET verb, not just the ones with buttons" do
      [ [ :post, job_applications_path ],
        [ :patch, job_application_path(owner_application) ],
        [ :delete, job_application_path(owner_application) ],
        [ :post, outreach_approvals_path ] ].each do |verb, path|
        send(verb, path)
        expect(response).not_to have_http_status(:ok), "#{verb.upcase} #{path} was allowed"
      end
    end

    it "can still sign out" do
      delete session_path
      get job_applications_path
      expect(response).to redirect_to(new_session_path)
    end
  end

  describe "the owner is unaffected" do
    before { sign_in_as(owner, "owner-password-12") }

    it "still sees their own data" do
      get job_applications_path
      expect(response.body).to include("OWNERSECRETEMPLOYER", "OWNERPRIVATENOTE")
    end

    it "does not see the sample account's rows" do
      demo.job_applications.create!(source: "sample", job_id: "s9",
                                    company_name: "Northwind Analytics", job_title: "Intern")
      get job_applications_path
      expect(response.body).not_to include("Northwind Analytics")
    end

    it "reads REAL drafts, not the samples" do
      get outreach_approvals_path
      expect(response.body).to include("A Real Company")
      expect(response.body).not_to include("Northwind Analytics")
    end

    it "can still write" do
      expect { post job_applications_path, params: { posting_key: @posting_key } }
        .to change(JobApplication, :count).by(1)
    end
  end
end
