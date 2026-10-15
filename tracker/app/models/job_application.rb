# One role you are tracking. Rails owns this table (tracker.job_applications).
#
# Named JobApplication rather than Application: the latter collides with
# Tracker::Application and reads confusingly next to ApplicationRecord.
#
# Company name, title and link are copied in at save time rather than joined from
# apply_queue on every read. That is deliberate denormalisation, not laziness: a
# posting leaves apply_queue when it is delisted (storage/prune.py deletes delisted
# postings outright) and the whole mart is dropped and rebuilt nightly by dbt. A
# role you applied to in March must still be readable in June, when the posting it
# came from is long gone.
class JobApplication < ApplicationRecord
  STATUSES = %w[saved applied interviewing rejected offer].freeze

  # Every row belongs to someone. Added when the demo account arrived: before
  # that this was a single-user app and ownership was implicit, which is exactly
  # the assumption a public login breaks.
  belongs_to :user

  # Mirrored by a Postgres CHECK constraint, because enforcing a vocabulary only in
  # the application layer is how public.outreach_tracker.status ended up with four
  # legal values that live in a Python tuple and nothing else.
  enum :status, STATUSES.index_by(&:itself), validate: true

  has_many :notes,
           class_name: "JobApplicationNote",
           dependent: :destroy,
           inverse_of: :job_application

  validates :source, :job_id, presence: true
  validates :job_id, uniqueness: { scope: [ :user_id, :source ] }

  before_validation { self.saved_at ||= Time.current }

  # When each status was first reached. `saved_at` is set on create; the others are
  # stamped once, on the transition, so moving backwards and forwards does not
  # rewrite history.
  STATUS_TIMESTAMPS = {
    "applied" => :applied_at,
    "interviewing" => :interviewing_at,
    "rejected" => :closed_at,
    "offer" => :closed_at
  }.freeze

  def advance_to!(new_status)
    column = STATUS_TIMESTAMPS[new_status.to_s]
    attrs = { status: new_status }
    attrs[column] = Time.current if column && self[column].nil?
    update!(attrs)
  end

  def key
    "#{source}:#{job_id}"
  end

  # The warehouse row this came from, if it is still there. Nil is a normal
  # outcome, not an error - see the note at the top about delisting.
  def posting
    @posting ||= Posting.find_by(source: source, job_id: job_id)
  end

  def self.for_posting(posting)
    find_by(source: posting.source, job_id: posting.job_id)
  end

  scope :open_roles, -> { where(status: %w[saved applied interviewing]) }
end
