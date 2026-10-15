class ApplicationController < ActionController::Base
  include Authentication
  allow_browser versions: :modern
  stale_when_importmap_changes

  # The demo account is read-only, and it is enforced HERE rather than by hiding
  # buttons. A hidden button is a suggestion; a rejected request is a rule. Any
  # non-GET from the demo account is refused before it reaches a controller
  # action, so a forgotten view, a stale form or a hand-crafted curl all hit the
  # same wall.
  before_action :refuse_writes_in_demo_mode

  private

  def demo_mode?
    Current.user&.demo?
  end
  helper_method :demo_mode?

  def refuse_writes_in_demo_mode
    return unless demo_mode?
    return if request.get? || request.head?
    # Signing out is the one thing a demo visitor must still be able to do.
    return if controller_name == "sessions" && action_name == "destroy"

    respond_to do |format|
      format.turbo_stream { head :forbidden }
      format.html do
        redirect_back fallback_location: postings_path,
                      alert: "This is the sample account, so nothing here can be changed."
      end
      format.any { head :forbidden }
    end
  end
end
