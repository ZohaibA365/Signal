require "rails_helper"

# The approval flow, end to end on the Rails side.
#
# The other half - that agent/show_drafts.py refuses to mark a draft sent without a
# matching approval - is Python, so it is tested in pytest
# (tests/test_send_guard.py). Testing it here would prove that Ruby agrees with
# itself.
RSpec.describe "The approval flow" do
  ORIGINAL = "Subject: TD Bank's hiring\n\nHi,\n\nOne true thing.\n\nAlex".freeze

  # Committed in before(:all). A `let!` would insert through `primary` inside the
  # example transaction, and the `warehouse` session - a different Postgres session
  # reading committed data - would not see it. See spec/support/warehouse.rb.
  before(:all) do
    Warehouse.reset!
    @draft_key = Warehouse.draft(company_name: "TD Bank", job_title: "Data Engineer Intern",
                                 draft_email: ORIGINAL).key
  end

  after(:all) { Warehouse.reset! }

  # Re-read per example, and the text put back after any example that redrafts,
  # since redraft! commits.
  let(:draft) { OutreachDraft.find_by_key(@draft_key) }

  after { Warehouse.redraft!(draft, ORIGINAL) if draft&.draft_email != ORIGINAL }

  before { sign_in }

  it "lists the drafts awaiting a decision" do
    get outreach_approvals_path
    expect(response).to have_http_status(:ok)
    expect(response.body).to include("TD Bank", "1 awaiting")
  end

  it "says who wrote the prose, because it changes how closely to read it" do
    get outreach_approvals_path
    expect(response.body).to include("model-written, verified")
  end

  describe "approving" do
    # The browser submits a textarea with CRLF line breaks while the agent wrote LF.
    # This is the exact shape that made every approval read as stale before the
    # canonical form existed, so the request spec sends it the way a browser would.
    it "accepts an unedited draft submitted with CRLF line endings" do
      post outreach_approvals_path, params: {
        draft_key: draft.key, decision: "approved",
        approved_text: draft.draft_email.gsub("\n", "\r\n")
      }

      approval = OutreachApproval.for_key(draft.key)
      expect(approval).to be_approved
      expect(approval.stale_for?(draft)).to be(false),
                                            "an unedited draft must not read as stale"
    end

    it "stores an edit, and the edit is what is authorised" do
      edited = "Subject: TD Bank's hiring\r\n\r\nHi,\r\n\r\nMy own words.\r\n\r\nAlex"
      post outreach_approvals_path, params: {
        draft_key: draft.key, decision: "approved", approved_text: edited
      }

      approval = OutreachApproval.for_key(draft.key)
      expect(approval.approved_text).to eq(OutreachApproval.canonical(edited))
      expect(approval.approved_text).to include("My own words")
    end

    it "shows the command that will send it" do
      post outreach_approvals_path, params: {
        draft_key: draft.key, decision: "approved", approved_text: draft.draft_email
      }
      get outreach_approvals_path
      expect(response.body).to include("--mark-sent #{draft.key}")
    end
  end

  describe "rejecting" do
    it "records the rejection without storing text" do
      post outreach_approvals_path, params: { draft_key: draft.key, decision: "rejected" }

      approval = OutreachApproval.for_key(draft.key)
      expect(approval).to be_rejected
      expect(approval.approved_text).to be_nil
    end
  end

  describe "when the agent redrafts after an approval" do
    it "shows the approval as stale rather than letting it stand" do
      post outreach_approvals_path, params: {
        draft_key: draft.key, decision: "approved", approved_text: draft.draft_email
      }
      Warehouse.redraft!(draft, "Subject: different words\n\nHi.")

      get outreach_approvals_path
      expect(response.body).to include("redrafted this after you approved it")
    end
  end

  describe "bad input" do
    it "refuses a decision that is not one" do
      post outreach_approvals_path, params: { draft_key: draft.key, decision: "maybe" }
      expect(OutreachApproval.count).to eq(0)
      expect(flash[:alert]).to be_present
    end

    it "refuses a draft that is not in the warehouse" do
      post outreach_approvals_path, params: { draft_key: "company_board:nope:1", decision: "approved" }
      expect(OutreachApproval.count).to eq(0)
      expect(flash[:alert]).to be_present
    end
  end

  # Nothing here may write to the warehouse. The approval is a Rails-owned row, and
  # outreach_tracker.status is the agent's and the send command's business.
  it "never touches the draft row" do
    before_status = draft.status
    post outreach_approvals_path, params: {
      draft_key: draft.key, decision: "approved", approved_text: draft.draft_email
    }
    expect(OutreachDraft.find_by_key(draft.key).status).to eq(before_status)
  end
end
