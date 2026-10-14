# The tables Rails owns. All three live in the `tracker` schema and nothing here
# touches `public`.
#
# Two rules are enforced by what this migration does NOT do.
#
# No foreign key to `public`. These tables key on (source, job_id) to match the
# warehouse's identity for a posting, but there is no FK to apply_queue, because
# dbt DROPS and recreates that table every night - an FK would either block the
# drop or cascade into deleting this data. Referential integrity across that
# boundary is by convention, deliberately.
#
# No name reused from `public`. `tracker.outreach_approvals`, never
# `tracker.outreach_tracker`: with `tracker` first on a connection's search_path a
# same-named table would silently shadow the real one, which is the reverse of the
# trick service/sandbox.py uses on purpose.
# The table is `job_applications`, not `applications`, because the model over it
# would otherwise have to be called `Application` - a name Rails already uses for
# the app class (Tracker::Application) and the base classes ApplicationRecord and
# ApplicationController. It resolves, but it reads badly and some gems reference
# ::Application. `JobApplication` is unambiguous and costs nothing.
class CreateTrackerTables < ActiveRecord::Migration[8.1]
  def change
    # In production the schema is created by script/setup_database_role.sql, run
    # once by hand with owner credentials. This branch exists for a fresh test
    # database, where Rails owns everything and no such script has run.
    #
    # Checked explicitly rather than with CREATE SCHEMA IF NOT EXISTS, because
    # Postgres tests the CREATE privilege on the database BEFORE the IF NOT EXISTS
    # short-circuits. The signal_rails role deliberately has no CREATE on the
    # database, so the unconditional form fails with "permission denied for
    # database neondb" even when the schema is already there.
    unless connection.select_value("SELECT 1 FROM pg_namespace WHERE nspname = 'tracker'")
      execute "CREATE SCHEMA tracker"
    end

    create_table :job_applications, primary_key: :id do |t|
      # The warehouse's identity for a posting. Not an FK - see the note above.
      t.string :source, null: false
      t.string :job_id, null: false

      t.string :status, null: false, default: "saved"

      # Denormalised at save time, on purpose. A posting can leave apply_queue
      # entirely - prune.py deletes delisted postings, and dbt rebuilds the mart
      # nightly - and a tracker entry for a role you applied to must survive that.
      # Without these columns the row would become unreadable the moment the
      # posting disappeared.
      t.string :company_name
      t.string :job_title
      t.string :link_url

      t.datetime :saved_at,         null: false
      t.datetime :applied_at
      t.datetime :interviewing_at
      t.datetime :closed_at

      t.timestamps
    end

    add_index :job_applications, [ :source, :job_id ], unique: true
    add_index :job_applications, :status

    # `public.outreach_tracker.status` has no CHECK constraint, and the allowed
    # values live only in a Python tuple (agent/schemas.py:22). That is a weakness
    # worth not repeating, so the vocabulary here is enforced by the database as
    # well as by the Rails enum.
    add_check_constraint :job_applications,
                         "status IN ('saved','applied','interviewing','rejected','offer')",
                         name: "job_applications_status_check"

    create_table :job_application_notes do |t|
      # This FK is fine: both sides are inside `tracker`.
      t.references :job_application, null: false, foreign_key: true
      t.text :body, null: false
      t.timestamps
    end

    create_table :outreach_approvals, primary_key: :id do |t|
      t.string :source, null: false
      t.string :job_id, null: false

      t.string :decision, null: false

      # The text actually approved. When you edit a draft before approving, this
      # holds the edited version and the fingerprint below is computed over it -
      # so what was approved is recorded, not merely which row was approved.
      t.text :approved_text

      # sha256 of the draft at the moment of approval.
      #
      # agent/tools.py's upsert uses coalesce, so a draft column can still hold
      # text from an earlier run (service/app.py:216-221 documents the same trap).
      # Pinning the approval to a hash means that if the agent redrafts afterwards,
      # the approval is recognisably stale instead of silently authorising text
      # nobody read. The send guard in agent/show_drafts.py compares against
      # encode(sha256(draft_email::bytea), 'hex'), so this must stay hex sha256.
      t.string :draft_fingerprint

      t.datetime :decided_at, null: false

      t.timestamps
    end

    add_index :outreach_approvals, [ :source, :job_id ], unique: true
    add_index :outreach_approvals, :decision

    add_check_constraint :outreach_approvals,
                         "decision IN ('approved','rejected')",
                         name: "outreach_approvals_decision_check"

    # An approval must carry the text and hash it approved; a rejection need not.
    add_check_constraint :outreach_approvals,
                         "decision <> 'approved' OR (approved_text IS NOT NULL AND draft_fingerprint IS NOT NULL)",
                         name: "outreach_approvals_approved_needs_text_check"
  end
end
