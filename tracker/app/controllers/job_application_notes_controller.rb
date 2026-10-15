# Notes on a tracked role.
#
# Append-only by habit rather than by constraint: a note is a record of what
# happened on a date, and editing one later loses the thing that makes it useful.
# Deleting is allowed because a note added to the wrong row is just a mistake.
class JobApplicationNotesController < ApplicationController
  before_action :set_application

  def create
    @note = @application.notes.new(note_params)

    if @note.save
      respond_to do |format|
        format.turbo_stream
        format.html { redirect_to job_applications_path }
      end
    else
      redirect_to job_applications_path, alert: "A note needs some text."
    end
  end

  def destroy
    @note = @application.notes.find(params[:id])
    @note.destroy!

    respond_to do |format|
      format.turbo_stream
      format.html { redirect_to job_applications_path }
    end
  end

  private

  def set_application
    @application = Current.user.job_applications.find(params[:job_application_id])
  end

  def note_params
    params.require(:job_application_note).permit(:body)
  end
end
