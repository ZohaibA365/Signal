# Demo mode: a second account that sees only seeded sample data.
#
# The app was built single-user, so nothing was scoped to an owner - every row in
# tracker.job_applications was implicitly "mine". A public demo account cannot
# exist safely until ownership is explicit, so that is what this adds.
#
# Drafts are the harder half. The approvals screen reads real agent output from
# public.outreach_tracker, which contains actual unsent emails. The demo account
# must never see those, and Rails cannot write fake rows into that table - it is
# the warehouse, and deliberately read-only. So demo drafts live in a table of
# their own that mirrors the columns the screen uses.
class AddDemoMode < ActiveRecord::Migration[8.1]
  def up
    add_column :users, :demo, :boolean, null: false, default: false
    add_index :users, :demo

    # Nullable first, backfilled, then constrained: there are already real rows,
    # and a NOT NULL column with no default would refuse to be added at all.
    add_reference :job_applications, :user, foreign_key: true, null: true
    add_reference :outreach_approvals, :user, foreign_key: true, null: true

    owner = execute(<<~SQL).first
      SELECT id FROM tracker.users WHERE demo = false ORDER BY id LIMIT 1
    SQL

    if owner
      execute "UPDATE tracker.job_applications SET user_id = #{owner['id']} WHERE user_id IS NULL"
      execute "UPDATE tracker.outreach_approvals SET user_id = #{owner['id']} WHERE user_id IS NULL"
    end

    # Only safe once every existing row has an owner.
    change_column_null :job_applications, :user_id, false
    change_column_null :outreach_approvals, :user_id, false

    # The unique keys were global; they have to be per-user now, or the demo
    # account saving a posting would collide with the real account having saved
    # the same one.
    remove_index :job_applications, column: [ :source, :job_id ]
    add_index :job_applications, [ :user_id, :source, :job_id ], unique: true
    remove_index :outreach_approvals, column: [ :source, :job_id ]
    add_index :outreach_approvals, [ :user_id, :source, :job_id ], unique: true

    # Sample drafts for the demo account. Same shape as the columns the approvals
    # screen reads out of public.outreach_tracker, so one view renders either.
    create_table :demo_drafts do |t|
      t.string :source, null: false
      t.string :job_id, null: false
      t.string :company_name
      t.string :job_title
      t.string :status, null: false, default: "email_drafted"
      t.text :draft_email
      t.text :draft_connection
      t.text :draft_followup
      t.string :draft_source
      t.timestamps
    end
    add_index :demo_drafts, [ :source, :job_id ], unique: true
  end

  def down
    drop_table :demo_drafts
    remove_index :job_applications, column: [ :user_id, :source, :job_id ]
    remove_index :outreach_approvals, column: [ :user_id, :source, :job_id ]
    add_index :job_applications, [ :source, :job_id ], unique: true
    add_index :outreach_approvals, [ :source, :job_id ], unique: true
    remove_reference :job_applications, :user
    remove_reference :outreach_approvals, :user
    remove_column :users, :demo
  end
end
