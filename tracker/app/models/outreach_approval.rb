require "digest"

# Your decision on one agent-written draft. Rails owns this
# (tracker.outreach_approvals).
#
# ---------------------------------------------------------------------------
# WHY THIS IS A SEPARATE TABLE AND NOT A STATUS
#
# The obvious design is to add 'approved' to public.outreach_tracker.status. It
# would break three things, and agent/tools.py:41-49 already says so:
#
#   "a send_email(cur, job_id) that refuses unless the row is already at
#    'email_drafted' AND carries an explicit human approval marker written by a
#    separate command a person runs."
#
# A conjunction - two facts, not one mutated field. Concretely:
#
#   1. agent/schemas.py:35 derives ALREADY_DRAFTED by SLICING the STATUSES tuple,
#      so inserting a value silently changes which statuses block re-drafting.
#      The order of that tuple is semantic.
#   2. agent/show_drafts.py:148 guards `AND status = 'email_drafted'`; an
#      intermediate status makes that UPDATE match zero rows.
#   3. agent/schemas.py:183-189 returns "unknown status" for anything outside
#      STATUSES, which would block drafting altogether.
#
# And outreach_tracker is warehouse-owned: Rails has SELECT on it and nothing more.
# ---------------------------------------------------------------------------
class OutreachApproval < ApplicationRecord
  DECISIONS = %w[approved rejected].freeze

  enum :decision, DECISIONS.index_by(&:itself), validate: true

  validates :source, :job_id, presence: true
  validates :job_id, uniqueness: { scope: :source }
  validates :approved_text, :draft_fingerprint, presence: true, if: :approved?

  before_validation { self.decided_at ||= Time.current }

  def key
    "#{source}:#{job_id}"
  end

  def self.for_key(key)
    source, job_id = key.to_s.split(":", 2)
    find_by(source: source, job_id: job_id)
  end

  # The one canonical form a fingerprint is ever taken over.
  #
  # This exists because a draft that was NOT edited must still fingerprint equal to
  # the text in the database, and two things get in the way:
  #
  #   HTML form submission normalises textarea line breaks to CRLF, while the agent
  #   wrote the draft with LF. Without normalising, every approval of an unedited
  #   draft would be instantly "stale" - the failure would look like the staleness
  #   check working, which is the worst kind of bug.
  #
  #   A <textarea> element ignores one newline straight after its opening tag, so
  #   the value round-trips with a leading blank line in some clients.
  #
  # agent/show_drafts.py applies the SAME transformation in SQL before hashing.
  # If one side changes, both must. They are checked against each other by the
  # approval-flow spec.
  def self.canonical(text)
    text.to_s.gsub(/\r\n?/, "\n").strip
  end

  def self.fingerprint_of(text)
    Digest::SHA256.hexdigest(canonical(text))
  end

  # Record a decision on a draft, pinning an approval to the exact text approved.
  #
  # `text` is what you actually approved - the draft as written, or your edit of
  # it. The fingerprint is computed over THAT, so an edited approval authorises the
  # edited words and nothing else.
  def self.decide!(draft, decision:, text: nil)
    approved = decision.to_s == "approved"
    body = approved ? canonical(text.presence || draft.draft_email) : nil

    record = find_or_initialize_by(source: draft.source, job_id: draft.job_id)
    record.assign_attributes(
      decision: decision,
      approved_text: body,
      draft_fingerprint: body && Digest::SHA256.hexdigest(body),
      decided_at: Time.current
    )
    record.save!
    record
  end

  # Has the agent redrafted since this was approved?
  #
  # If so the approval is STALE: it authorised words that are no longer what the
  # agent would send. The send guard in agent/show_drafts.py compares the same
  # hash, so a stale approval also fails there - this is the UI's chance to say why
  # before you wonder about it.
  def stale_for?(draft)
    return false unless approved?

    draft_fingerprint != self.class.fingerprint_of(draft.draft_email)
  end
end
