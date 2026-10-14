require "digest"

module JobApplicationsHelper
  STATUS_TEXT = {
    "saved" => "text-gray-600",
    "applied" => "text-sky-700",
    "interviewing" => "text-indigo-700",
    "offer" => "text-emerald-700",
    "rejected" => "text-gray-400"
  }.freeze

  def status_text_class(status)
    STATUS_TEXT.fetch(status, "text-gray-600")
  end

  def status_select_class(status)
    "#{STATUS_TEXT.fetch(status, 'text-gray-600')} font-medium"
  end

  # One id for the save-or-status control, addressable from either side.
  #
  # Keyed on (source, job_id) rather than the application id, because the control
  # exists on the browse page BEFORE any application row does - a Save has to be
  # able to replace the button that triggered it. Falls back to the application
  # when the posting is gone from the warehouse, which is a normal state.
  def control_dom_id(posting:, application: nil)
    key = posting&.key || "#{application.source}:#{application.job_id}"
    "tracker_control_#{Digest::SHA256.hexdigest(key)[0, 16]}"
  end
end
