class User < ApplicationRecord
  has_secure_password
  has_many :sessions, dependent: :destroy
  has_many :job_applications, dependent: :destroy
  has_many :outreach_approvals, dependent: :destroy

  normalizes :email_address, with: ->(e) { e.strip.downcase }

  # The demo account is readable by anyone with the credentials printed on the
  # sign-in page. It sees only seeded sample rows and cannot write anything -
  # enforced in ApplicationController, not merely in the views, because a view
  # that forgets to hide a button is a bug and a controller that refuses a POST
  # is a rule.
  scope :real, -> { where(demo: false) }

  def demo? = demo
end
