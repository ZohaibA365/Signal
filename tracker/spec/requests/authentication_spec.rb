require "rails_helper"

# This app shows the warehouse, your application statuses and draft emails with
# your name on them. Every page except the login form must require a session.
RSpec.describe "Authentication" do
  it "sends every page to the login form when signed out" do
    [ root_path, postings_path, job_applications_path, outreach_approvals_path ].each do |path|
      get path
      expect(response).to redirect_to(new_session_path), "#{path} was reachable signed out"
    end
  end

  it "refuses a write when signed out" do
    expect { post job_applications_path, params: { posting_key: "a:b" } }
      .not_to change(JobApplication, :count)
    expect(response).to redirect_to(new_session_path)
  end

  it "lets the one user in and out" do
    sign_in
    get postings_path
    expect(response).to have_http_status(:ok)

    delete session_path
    get postings_path
    expect(response).to redirect_to(new_session_path)
  end

  it "rejects a wrong password" do
    post session_path, params: { email_address: test_user.email_address, password: "wrong" }
    get postings_path
    expect(response).to redirect_to(new_session_path)
  end

  # Password reset was removed with PasswordsController: it needs Action Mailer to
  # deliver a token, and Action Mailer is deliberately not installed.
  it "has no password-reset route to half-work" do
    expect { new_password_path }.to raise_error(NameError)
  end
end
