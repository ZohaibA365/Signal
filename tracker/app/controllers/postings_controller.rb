# Browse postings.
#
# Renders; it does not decide. Every rule about which rows come back lives in
# PostingFilter, so the filter form, the "show more" link and anything added later
# cannot drift apart.
class PostingsController < ApplicationController
  def index
    @filter = PostingFilter.new(filter_params)
    @postings = @filter.results.to_a

    # Which of these are already in your tracker, in one query rather than one per
    # row. Without this the list would N+1 across 50 rows on every keystroke.
    @tracked = JobApplication
               .where(source: @postings.map(&:source), job_id: @postings.map(&:job_id))
               .index_by(&:key)

    # Offered as filter options rather than hardcoded, so the list reflects what is
    # actually in the corpus today.
    @states = Rails.cache.fetch("posting_states", expires_in: 1.hour) do
      Posting.where.not(location_state: nil).distinct.pluck(:location_state).sort
    end

    # Corpus shape, for the copy that explains why the default view is small.
    # Computed, not written into the template: the corpus roughly doubled in the
    # six weeks to 2026-10-07, so any number typed into a view is already wrong.
    @corpus = Rails.cache.fetch("corpus_shape", expires_in: 1.hour) do
      {
        total: Posting.count,
        unscored: Posting.where(recommendation: "not yet scored").count,
        actionable: Posting.default_browse.clickable.count
      }
    end
  end

  private

  def filter_params
    params.permit(*PostingFilter::ATTRIBUTES).to_h.symbolize_keys
  end
end
