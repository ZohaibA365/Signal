require "spec_helper"
ENV["RAILS_ENV"] ||= "test"
require_relative "../config/environment"

abort("The Rails environment is running in production mode!") if Rails.env.production?

require "rspec/rails"
require "shoulda-matchers"

Dir[Rails.root.join("spec/support/**/*.rb")].sort.each { |f| require f }

begin
  ActiveRecord::Migration.maintain_test_schema!
rescue ActiveRecord::PendingMigrationError => e
  abort e.to_s.strip
end

RSpec.configure do |config|
  config.fixture_paths = [ Rails.root.join("spec/fixtures") ]
  config.use_transactional_fixtures = true
  config.infer_spec_type_from_file_location!
  config.filter_rails_from_backtrace!

  # The warehouse stand-ins are created and seeded ONCE, committed, before any
  # example transaction opens. See spec/support/warehouse.rb for why they cannot
  # be per-example: the two connections are separate Postgres sessions, so an
  # uncommitted insert on `primary` is invisible to `warehouse`.
  config.before(:suite) do
    Warehouse.load_schema!
    Warehouse.reset!
  end

  # Signing in. There is one user and session-based auth, so a request spec that
  # does not do this gets a redirect to the login page - which is itself worth
  # asserting, and one spec does.
  config.include(Module.new do
    def sign_in(user = test_user)
      post session_path, params: { email_address: user.email_address, password: "a-test-password-12" }
      follow_redirect! if response.redirect?
      user
    end

    def test_user
      @test_user ||= User.create!(email_address: "spec@example.com", password: "a-test-password-12")
    end
  end, type: :request)
end

Shoulda::Matchers.configure do |config|
  config.integrate do |with|
    with.test_framework :rspec
    with.library :rails
  end
end
