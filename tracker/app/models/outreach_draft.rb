require "digest"

# A draft the outreach agent wrote, as stored by the Python side.
#
# Reads `public.outreach_tracker`, which is NOT a dbt model - it is declared in
# storage/schema.sql and written by agent/tools.py:205-218 in a single
# INSERT ... ON CONFLICT DO UPDATE. Rails only ever reads it. Approval state lives
# in tracker.outreach_approvals, a table Rails owns.
#
# ---------------------------------------------------------------------------
# Two traps live in this table, and both are handled here rather than in a view.
#
# 1. The draft columns can hold STALE TEXT.
#
#    The upsert uses coalesce:
#
#        draft_email = coalesce(EXCLUDED.draft_email, outreach_tracker.draft_email)
#
#    so a status change never erases draft text - which is the right behaviour for
#    the agent and a hazard for an approval UI. A row can therefore carry text from
#    a previous run. service/app.py:216-221 documents the same problem: reading the
#    draft columns unconditionally once showed an old draft beside the words
#    "skipping to avoid a duplicate". So presence of `draft_email` is NOT evidence
#    that a draft is current, and nothing here treats it as such - see
#    `fingerprint`, which is what an approval is pinned to.
#
# 2. `status` has no CHECK constraint.
#
#    The allowed values exist only in Python (agent/schemas.py:22). Rails must not
#    invent a new one. `ALREADY_DRAFTED` there is derived by SLICING that tuple, so
#    inserting a value would silently change which statuses block re-drafting, and
#    agent/show_drafts.py:148 guards on `status = 'email_drafted'` exactly.
#    Approval is a separate marker, which is the shape agent/tools.py:41-49 already
#    specifies.
# ---------------------------------------------------------------------------
class OutreachDraft < WarehouseRecord
  self.table_name  = "outreach_tracker"
  self.primary_key = [ :source, :job_id ]

  # Mirrors agent/schemas.py:22. Order is significant there; here it is only a
  # vocabulary, and Rails never writes any of it.
  STATUS_DRAFTED = "email_drafted".freeze

  scope :awaiting_decision, -> { where(status: STATUS_DRAFTED).order(updated_at: :desc) }

  def key
    "#{source}:#{job_id}"
  end

  def self.find_by_key(key)
    source, job_id = key.to_s.split(":", 2)
    return nil if source.blank? || job_id.blank?

    find_by(source: source, job_id: job_id)
  end

  # What an approval is pinned to.
  #
  # sha256 of the email text as it stands right now. An approval records this
  # value, so if the agent redrafts afterwards the stored fingerprint stops
  # matching and the approval is recognisably stale rather than silently
  # authorising text nobody read.
  #
  # Computed over `draft_email` only, because that is the body the send guard in
  # agent/show_drafts.py checks. It must stay byte-identical to the Postgres
  # expression used there:
  #
  #     encode(sha256(outreach_tracker.draft_email::bytea), 'hex')
  def fingerprint
    return nil if draft_email.blank?

    Digest::SHA256.hexdigest(draft_email)
  end

  # Whether the model or the deterministic template wrote this.
  # outreach/compose.py builds messages from SQL-derived records with no model
  # call; agent/draft.py lets the model write prose and keeps it only if
  # agent/verify.py can prove every figure. Worth showing in the UI, because it
  # tells you how much to trust the sentences around the numbers.
  def model_written?
    draft_source == "model"
  end
end
