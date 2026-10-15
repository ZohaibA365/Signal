namespace :tracker do
  desc "Save the pipeline's top recommendations into the tracker (idempotent)"
  task backfill: :environment do
    # Deliberately NOT part of db:seed.
    #
    # db:seed runs on every Render deploy, so putting this there would quietly
    # re-add every role you had removed from the tracker - and the removals are
    # decisions, not noise. This is a thing you run when you want it.
    #
    #   bin/rails tracker:backfill                 # apply now, up to 25
    #   bin/rails tracker:backfill DRY_RUN=1       # list them, write nothing
    #   bin/rails tracker:backfill RECOMMENDATION="worth a look" LIMIT=10
    #
    # Render's free plan has no shell, so run it locally against the same
    # database - tracker/.env already points there.
    dry_run = ENV["DRY_RUN"].present?
    wanted = ENV.fetch("RECOMMENDATION", "apply now")
    limit = Integer(ENV.fetch("LIMIT", 25), exception: false) || 25

    unless Posting::RECOMMENDATIONS.include?(wanted)
      abort "RECOMMENDATION must be one of: #{Posting::RECOMMENDATIONS.join(', ')}"
    end

    # The owner's account, never the sample one - the sample account is seeded by
    # db:seed and is read-only by design.
    owner = User.real.order(:id).first
    abort "no owner account found; run bin/rails db:seed first" if owner.nil?

    candidates = Posting.where(recommendation: wanted).clickable.order(:apply_rank).limit(limit).to_a
    # One query rather than one per row.
    already = owner.job_applications.where(source: candidates.map(&:source),
                                           job_id: candidates.map(&:job_id)).index_by(&:key)
    fresh = candidates.reject { |p| already.key?(p.key) }

    puts "#{candidates.size} posting(s) recommended #{wanted.inspect} with a direct link"
    puts "#{already.size} already tracked, #{fresh.size} to add"
    puts

    if fresh.empty?
      puts "Nothing to do."
      next
    end

    fresh.each do |posting|
      line = format("  %-28s %s", posting.company_name.to_s[0, 28], posting.job_title.to_s[0, 58])
      if dry_run
        puts "  would add#{line}"
        next
      end

      # Company, title and link are copied, not joined: dbt drops apply_queue
      # nightly and prune.py deletes delisted postings, so a tracked role has to
      # survive the posting disappearing.
      owner.job_applications.create!(
        source: posting.source, job_id: posting.job_id,
        company_name: posting.company_name, job_title: posting.job_title,
        link_url: posting.link_url, status: "saved", saved_at: Time.current
      )
      puts "  added  #{line}"
    end

    puts
    if dry_run
      puts "DRY_RUN: nothing was written."
    else
      puts "Tracker now holds #{owner.job_applications.count} role(s)."
      puts "Open /tracker to work through them."
    end
  end
end
