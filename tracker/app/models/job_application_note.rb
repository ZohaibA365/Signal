# A dated note on a tracked role. Rails owns this (tracker.job_application_notes).
#
# Append-only by habit rather than by constraint: notes are a log of what happened
# in a process, and editing one after the fact loses the thing that makes it useful.
class JobApplicationNote < ApplicationRecord
  belongs_to :job_application, inverse_of: :notes

  validates :body, presence: true

  scope :newest_first, -> { order(created_at: :desc) }
end
