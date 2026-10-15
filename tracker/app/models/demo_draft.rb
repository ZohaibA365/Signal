require "digest"

# Sample agent output, for the demo account only.
#
# Mirrors the columns the approvals screen reads out of public.outreach_tracker,
# so one view renders either. It exists because the real table holds actual
# unsent emails written about real companies, and Rails cannot put fake rows in
# there anyway - that table belongs to the pipeline and this app is read-only
# against it.
#
# Unlike OutreachDraft this one IS writable by the app, because it is seeded by
# db/seeds.rb rather than by the agent. Nothing in a request writes to it.
class DemoDraft < ApplicationRecord
  STATUS_DRAFTED = "email_drafted".freeze

  scope :awaiting_decision, -> { where(status: STATUS_DRAFTED).order(updated_at: :desc) }

  def key = "#{source}:#{job_id}"

  def self.find_by_key(key)
    source, job_id = key.to_s.split(":", 2)
    return nil if source.blank? || job_id.blank?

    find_by(source: source, job_id: job_id)
  end

  # Same contract as OutreachDraft#fingerprint, so staleness behaves identically
  # whichever account is looking.
  def fingerprint
    return nil if draft_email.blank?

    OutreachApproval.fingerprint_of(draft_email)
  end

  def model_written? = draft_source == "model"
end
