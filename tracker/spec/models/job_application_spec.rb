require "rails_helper"

RSpec.describe JobApplication do
  subject(:application) do
    described_class.new(source: "company_board", job_id: "abc",
                        company_name: "Acme", job_title: "Data Engineer")
  end

  it { is_expected.to validate_presence_of(:source) }
  it { is_expected.to validate_presence_of(:job_id) }
  it { is_expected.to have_many(:notes).dependent(:destroy) }

  it "stamps saved_at on create so a row always has a start date" do
    application.save!
    expect(application.saved_at).to be_present
  end

  it "is unique per posting" do
    application.save!
    duplicate = described_class.new(source: "company_board", job_id: "abc", job_title: "x")
    expect(duplicate).not_to be_valid
  end

  describe "#advance_to!" do
    before { application.save! }

    it "stamps the timestamp for the status reached" do
      application.advance_to!("applied")
      expect(application.applied_at).to be_present
      expect(application.interviewing_at).to be_nil
    end

    # The point of these columns is WHEN something first happened, not what the row
    # currently says. Moving backwards and forwards must not rewrite history.
    it "does not rewrite a timestamp that is already set" do
      application.advance_to!("applied")
      first = application.applied_at

      application.advance_to!("interviewing")
      application.advance_to!("applied")

      expect(application.reload.applied_at).to eq(first)
      expect(application.interviewing_at).to be_present
    end

    it "treats rejected and offer as the same closing date" do
      application.advance_to!("rejected")
      expect(application.closed_at).to be_present
    end

    # `enum ... validate: true` adds a validation rather than raising on
    # assignment, so this is RecordInvalid. The controller rescues both that and
    # ArgumentError, because an older enum would have raised the latter.
    it "refuses a status outside the vocabulary" do
      expect { application.advance_to!("hired") }.to raise_error(ActiveRecord::RecordInvalid)
    end
  end

  # The vocabulary is enforced twice on purpose. public.outreach_tracker.status has
  # no CHECK constraint and keeps its legal values only in a Python tuple
  # (agent/schemas.py), which is the weakness this deliberately does not repeat.
  it "refuses a bad status in the database, not only in Ruby" do
    application.save!
    expect {
      described_class.connection.execute(
        "UPDATE tracker.job_applications SET status = 'hired' WHERE id = #{application.id}"
      )
    }.to raise_error(ActiveRecord::StatementInvalid, /check constraint/i)
  end

  describe "#posting" do
    before(:all) do
      Warehouse.reset!
      @posting_key = Warehouse.posting.key
    end

    after(:all) { Warehouse.reset! }

    it "finds the warehouse row it came from" do
      posting = Posting.find_by_key(@posting_key)
      app = described_class.create!(source: posting.source, job_id: posting.job_id,
                                    company_name: posting.company_name, job_title: posting.job_title)
      expect(app.posting&.key).to eq(posting.key)
    end

    # A posting leaves apply_queue when it is delisted - prune.py deletes them
    # outright - and dbt rebuilds the whole mart nightly. A tracked role has to
    # survive that, which is why the company and title are copied in at save time.
    it "is nil when the posting has gone, and the row still reads" do
      app = described_class.create!(source: "company_board", job_id: "vanished",
                                    company_name: "Gone Inc", job_title: "Data Engineer")
      expect(app.posting).to be_nil
      expect(app.company_name).to eq("Gone Inc")
    end
  end
end
