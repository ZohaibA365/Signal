# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[8.1].define(version: 2026_10_14_060000) do
  create_schema "tracker"

  # These are extensions that must be enabled in order to support this database
  enable_extension "pg_catalog.plpgsql"

  create_table "job_application_notes", force: :cascade do |t|
    t.bigint "job_application_id", null: false
    t.text "body", null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["job_application_id"], name: "index_job_application_notes_on_job_application_id"
  end

  create_table "job_applications", force: :cascade do |t|
    t.string "source", null: false
    t.string "job_id", null: false
    t.string "status", default: "saved", null: false
    t.string "company_name"
    t.string "job_title"
    t.string "link_url"
    t.datetime "saved_at", null: false
    t.datetime "applied_at"
    t.datetime "interviewing_at"
    t.datetime "closed_at"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["source", "job_id"], name: "index_job_applications_on_source_and_job_id", unique: true
    t.index ["status"], name: "index_job_applications_on_status"
    t.check_constraint "status::text = ANY (ARRAY['saved'::character varying, 'applied'::character varying, 'interviewing'::character varying, 'rejected'::character varying, 'offer'::character varying]::text[])", name: "job_applications_status_check"
  end

  create_table "outreach_approvals", force: :cascade do |t|
    t.string "source", null: false
    t.string "job_id", null: false
    t.string "decision", null: false
    t.text "approved_text"
    t.string "draft_fingerprint"
    t.datetime "decided_at", null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["decision"], name: "index_outreach_approvals_on_decision"
    t.index ["source", "job_id"], name: "index_outreach_approvals_on_source_and_job_id", unique: true
    t.check_constraint "decision::text <> 'approved'::text OR approved_text IS NOT NULL AND draft_fingerprint IS NOT NULL", name: "outreach_approvals_approved_needs_text_check"
    t.check_constraint "decision::text = ANY (ARRAY['approved'::character varying, 'rejected'::character varying]::text[])", name: "outreach_approvals_decision_check"
  end

  create_table "sessions", force: :cascade do |t|
    t.bigint "user_id", null: false
    t.string "ip_address"
    t.string "user_agent"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["user_id"], name: "index_sessions_on_user_id"
  end

  create_table "users", force: :cascade do |t|
    t.string "email_address", null: false
    t.string "password_digest", null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["email_address"], name: "index_users_on_email_address", unique: true
  end

  add_foreign_key "job_application_notes", "job_applications"
  add_foreign_key "sessions", "users"
end
