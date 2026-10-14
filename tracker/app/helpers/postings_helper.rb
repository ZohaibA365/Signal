require "digest"

module PostingsHelper
  BADGE_TONES = {
    good: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
    ok: "bg-sky-50 text-sky-800 ring-sky-600/20",
    warn: "bg-amber-50 text-amber-800 ring-amber-600/20",
    bad: "bg-rose-50 text-rose-800 ring-rose-600/20",
    neutral: "bg-gray-50 text-gray-700 ring-gray-500/20"
  }.freeze

  # Tone carries meaning and nothing else here: green is "this helps you", red is
  # "this rules you out". The same discipline the agent console uses for its step
  # colours, so a badge cannot read as decorative.
  def posting_badge(text, tone = :neutral)
    tag.span text,
             class: "inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset #{BADGE_TONES.fetch(tone, BADGE_TONES[:neutral])}"
  end

  def recommendation_class(recommendation)
    case recommendation
    when "apply now" then "font-medium text-emerald-700"
    when "worth a look" then "text-sky-700"
    when "low priority" then "text-gray-500"
    when "skip" then "text-gray-400"
    else "text-gray-400"
    end
  end

  # apply_queue has no single-column primary key, so dom_id would raise. The
  # (source, job_id) pair is the identity, and it has to survive being a DOM id.
  #
  # A digest rather than parameterize: job ids contain colons, slashes and dots,
  # and parameterize flattens all of them to "-", so "lever:a.b" and "lever:a-b"
  # would become the same element id. Turbo replaces frames and stream targets by
  # id, so a collision would update the wrong row.
  def dom_id_for_posting(posting)
    "posting_#{Digest::SHA256.hexdigest(posting.key)[0, 16]}"
  end
end
