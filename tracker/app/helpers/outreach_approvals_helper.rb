require "digest"

module OutreachApprovalsHelper
  # outreach_tracker has no single-column primary key, so dom_id would raise.
  # A digest of (source, job_id) rather than parameterize: job ids contain colons,
  # slashes and dots, which parameterize flattens to "-", and Turbo replaces
  # targets by id - a collision would update the wrong card.
  def dom_id_for_draft(draft)
    "draft_#{Digest::SHA256.hexdigest(draft.key)[0, 16]}"
  end

  def dom_id_for_decision(draft)
    "decision_#{Digest::SHA256.hexdigest(draft.key)[0, 16]}"
  end

  # Border carries the state at a glance: amber for a stale approval, because that
  # is the one case that needs you to look again.
  def draft_border_class(draft, approval)
    return "border-gray-200" if approval.nil?
    return "border-amber-300" if approval.approved? && approval.stale_for?(draft)
    return "border-emerald-300" if approval.approved?

    "border-gray-200 opacity-70"
  end
end
