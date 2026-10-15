# The application tracker: what you have saved, and where each role stands.
class JobApplicationsController < ApplicationController
  before_action :set_application, only: [ :update, :destroy ]

  def index
    @applications = Current.user.job_applications.includes(:notes).order(updated_at: :desc)
    @by_status = @applications.group_by(&:status)
    @counts = Current.user.job_applications.group(:status).count
  end

  # Save a posting into the tracker.
  #
  # The company, title and link are COPIED from the warehouse row rather than
  # joined on every read. apply_queue is dropped and recreated by dbt nightly and
  # prune.py deletes delisted postings outright, so a role you applied to in March
  # must still be readable in June when the posting it came from is gone. See the
  # note at the top of JobApplication.
  def create
    posting = Posting.find_by_key(params[:posting_key])
    return redirect_back_with("That posting is no longer in the warehouse.") if posting.nil?

    @application = Current.user.job_applications
                          .find_or_initialize_by(source: posting.source, job_id: posting.job_id)
    @application.assign_attributes(
      company_name: posting.company_name,
      job_title: posting.job_title,
      link_url: posting.link_url,
      status: @application.new_record? ? "saved" : @application.status
    )
    @application.save!

    respond_with_control(posting)
  end

  def update
    @application.advance_to!(params.require(:job_application).fetch(:status))
    respond_with_control(@application.posting)
  rescue ArgumentError, ActiveRecord::RecordInvalid => e
    # enum validate: true raises on a value outside STATUSES, and the Postgres
    # CHECK constraint would refuse it even if that were bypassed.
    redirect_back_with("Could not change status: #{e.message}")
  end

  def destroy
    posting = @application.posting
    @application.destroy!
    respond_with_control(posting, removed: true)
  end

  private

  def set_application
    # scoped: a crafted id for someone else's row must 404, not load
    @application = Current.user.job_applications.find(params[:id])
  end

  # Both pages render the same control, so both get updated the same way: replace
  # just that element and leave the rest of the page - and its scroll position and
  # focus - alone.
  def respond_with_control(posting, removed: false)
    respond_to do |format|
      format.turbo_stream do
        @posting = posting
        @removed = removed
        render :control
      end
      format.html { redirect_back fallback_location: job_applications_path }
    end
  end

  def redirect_back_with(message)
    redirect_back fallback_location: postings_path, alert: message
  end
end
