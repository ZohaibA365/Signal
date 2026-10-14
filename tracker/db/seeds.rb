# The single user.
#
# This app has exactly one account on purpose. It shows your pipeline data, your
# application statuses and draft emails with your name on them, so it is a
# single-tenant tool and there is no sign-up path - SessionsController is the only
# controller that allows unauthenticated access.
#
# Credentials come from the environment rather than from this file, so no password
# ever enters git. Locally they live in tracker/.env (gitignored); on Render they
# are environment variables.
#
#     bin/rails db:seed
#
# Re-running is safe: it updates the password of the existing user rather than
# failing on the unique index, which is also how you reset a forgotten password
# (there is no reset-by-email flow - Action Mailer is deliberately not installed).

email = ENV["ADMIN_EMAIL"]
password = ENV["ADMIN_PASSWORD"]

if email.blank? || password.blank?
  abort <<~MSG
    db:seed needs ADMIN_EMAIL and ADMIN_PASSWORD.

    Add them to tracker/.env for local use, or set them in the Render dashboard.
    Nothing was created.
  MSG
end

if password.length < 12
  abort "ADMIN_PASSWORD must be at least 12 characters. Nothing was created."
end

user = User.find_or_initialize_by(email_address: email)
created = user.new_record?
user.password = password
user.save!

# Any existing browser sessions were authenticated against the old password.
user.sessions.destroy_all unless created

puts created ? "created user #{user.email_address}" : "updated password for #{user.email_address} (existing sessions revoked)"
puts "users in tracker.users: #{User.count}"
