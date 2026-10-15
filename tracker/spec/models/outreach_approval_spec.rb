require "rails_helper"

# The fingerprint is the whole point of this model, so most of this file is about
# it. An approval that silently stops matching is worse than no approval at all,
# because the UI would report "stale" and you would re-approve forever; and an
# approval that matches text it should not would authorise words nobody read.
RSpec.describe OutreachApproval do
  # Created in before(:all) and COMMITTED, because the draft is written through the
  # `primary` connection and read back through `warehouse` - two separate Postgres
  # sessions. A `let` would insert inside the example transaction, which the reader
  # session cannot see, and every example would find nil.
  before(:all) do
    Warehouse.reset!
    @draft_key = Warehouse.draft.key
  end

  after(:all) { Warehouse.reset! }

  # Re-read per example, so a test that redrafts does not leak a stale object.
  let(:draft) { OutreachDraft.find_by_key(@draft_key) }

  # Approvals belong to someone since the sample account arrived.
  let(:user) do
    User.create!(email_address: "owner@example.com", password: "owner-password-12")
  end

  describe ".canonical" do
    # This is the bug this method exists for. HTML form submission normalises
    # textarea line breaks to CRLF, while the agent wrote the draft with LF. Before
    # normalising, approving an UNEDITED draft produced a fingerprint that could
    # never match - and the symptom was the staleness banner appearing immediately,
    # which looks exactly like the check working correctly.
    it "treats CRLF and LF as the same text" do
      lf = "Subject: Hi\n\nOne line.\nAnother.\n"
      expect(described_class.canonical(lf.gsub("\n", "\r\n"))).to eq(described_class.canonical(lf))
    end

    it "treats a bare CR as a line break too" do
      expect(described_class.canonical("a\rb")).to eq("a\nb")
    end

    # A <textarea> ignores one newline straight after its opening tag, so values
    # round-trip with a leading blank line in some clients.
    it "ignores leading and trailing whitespace" do
      body = "Subject: Hi\n\nBody."
      expect(described_class.canonical("\n#{body}\n\n  ")).to eq(described_class.canonical(body))
    end

    it "does not collapse whitespace inside the text" do
      expect(described_class.canonical("a\n\n\nb")).to eq("a\n\n\nb")
    end
  end

  describe ".fingerprint_of" do
    it "is stable across every shape a browser can submit" do
      raw = draft.draft_email
      base = described_class.fingerprint_of(raw)

      [ raw.gsub("\n", "\r\n"), "\n#{raw}", "#{raw}\n\n", "\n#{raw.gsub("\n", "\r\n")}  " ]
        .each { |variant| expect(described_class.fingerprint_of(variant)).to eq(base) }
    end

    it "changes when the words change" do
      expect(described_class.fingerprint_of("#{draft.draft_email} PS")).
        not_to eq(described_class.fingerprint_of(draft.draft_email))
    end

    # agent/show_drafts.py recomputes this in SQL. If the two ever disagree the
    # send guard refuses every approval, so the SQL expression is asserted here
    # against the Ruby one rather than trusted to stay in step.
    it "matches the SQL expression agent/show_drafts.py uses" do
      sql_expression = <<~SQL.squish
        encode(sha256((
          btrim(replace(replace($1, chr(13) || chr(10), chr(10)), chr(13), chr(10)),
                ' ' || chr(9) || chr(10))
        )::bytea), 'hex')
      SQL

      [ draft.draft_email,
        draft.draft_email.gsub("\n", "\r\n"),
        "\n#{draft.draft_email}\n" ].each do |text|
        from_sql = ActiveRecord::Base.connection.select_value(
          ActiveRecord::Base.sanitize_sql_array([ "SELECT #{sql_expression.sub('$1', '?')}", text ])
        )
        expect(from_sql).to eq(described_class.fingerprint_of(text)),
                            "SQL and Ruby disagree for #{text.inspect[0, 40]}"
      end
    end
  end

  describe ".decide!" do
    it "pins an approval to the draft text when nothing was edited" do
      approval = described_class.decide!(draft, decision: "approved", user: user)

      expect(approval).to be_approved
      expect(approval.stale_for?(draft)).to be(false)
      expect(approval.approved_text).to eq(described_class.canonical(draft.draft_email))
    end

    it "pins an approval to the EDITED text, not the original" do
      edited = "#{draft.draft_email}\n\nPS: one more thing."
      approval = described_class.decide!(draft, decision: "approved", user: user, text: edited)

      expect(approval.approved_text).to eq(described_class.canonical(edited))
      expect(approval.draft_fingerprint).to eq(described_class.fingerprint_of(edited))
      # The approval covers the edit, so measured against the ORIGINAL draft it is
      # correctly stale - that is not a bug, it is the point.
      expect(approval.stale_for?(draft)).to be(true)
    end

    it "stores no text or fingerprint for a rejection" do
      approval = described_class.decide!(draft, decision: "rejected", user: user)

      expect(approval.approved_text).to be_nil
      expect(approval.draft_fingerprint).to be_nil
      expect(approval.stale_for?(draft)).to be(false)
    end

    it "replaces an earlier decision on the same posting rather than duplicating" do
      described_class.decide!(draft, decision: "rejected", user: user)
      expect { described_class.decide!(draft, decision: "approved", user: user) }
        .not_to change(described_class, :count)
      expect(described_class.for_key(draft.key, user: user)).to be_approved
    end
  end

  describe "#stale_for?" do
    # redraft! commits, so the original text has to be put back or the examples
    # after this one would run against different words.
    after { Warehouse.redraft!(draft, @original_text) if @original_text }

    it "becomes stale when the agent redrafts underneath an approval" do
      @original_text = draft.draft_email
      approval = described_class.decide!(draft, decision: "approved", user: user)
      redrafted = Warehouse.redraft!(draft, "Subject: different words entirely\n\nHi.")

      expect(approval.stale_for?(redrafted)).to be(true)
    end

    it "is never stale for a rejection, whatever the text does" do
      @original_text = draft.draft_email
      approval = described_class.decide!(draft, decision: "rejected", user: user)
      redrafted = Warehouse.redraft!(draft, "anything else")

      expect(approval.stale_for?(redrafted)).to be(false)
    end
  end

  describe "the database, not just the model" do
    it "refuses a decision outside the vocabulary even via raw SQL" do
      described_class.decide!(draft, decision: "approved", user: user)

      expect {
        described_class.connection.execute(
          "UPDATE tracker.outreach_approvals SET decision = 'maybe'"
        )
      }.to raise_error(ActiveRecord::StatementInvalid, /check constraint/i)
    end

    it "refuses an approval with no text to point at" do
      expect {
        described_class.connection.execute(<<~SQL)
          INSERT INTO tracker.outreach_approvals
            (user_id, source, job_id, decision, decided_at, created_at, updated_at)
          VALUES (#{user.id}, 's', 'j', 'approved', NOW(), NOW(), NOW())
        SQL
      }.to raise_error(ActiveRecord::StatementInvalid, /check constraint/i)
    end
  end
end
