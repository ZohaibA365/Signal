# Browse postings. Stage 1 is deliberately the smallest thing that proves the
# read path works end to end: authentication, the warehouse connection, and the
# read-only model. Filtering, sorting and search arrive in Stage 2.
class PostingsController < ApplicationController
  def index
    # `default_browse` rather than everything. Measured 2026-10-14: apply_queue
    # holds 70,770 rows of which 64,346 are "not yet scored", because enrichment
    # is incremental and costs money. Opening on all of them would show mostly
    # blanks, and the table has no indexes, so an unscoped sort seq-scans 32 MB.
    @postings = Posting.default_browse
                       .clickable
                       .order(:apply_rank)
                       .limit(25)

    @counts = Posting.default_browse.group(:recommendation).count
  end
end
