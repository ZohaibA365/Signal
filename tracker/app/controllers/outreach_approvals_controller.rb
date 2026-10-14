# The approval queue: the agent's drafts, and your decision on each.
#
# Nothing here sends anything. There is no send path in this project at all -
# agent/tools.py:41-49 specifies that sending stays a separate command a person
# runs, and that it must refuse unless a draft is at 'email_drafted' AND carries an
# explicit human approval marker. This controller writes that marker.
class OutreachApprovalsController < ApplicationController
  def index
    @drafts = OutreachDraft.awaiting_decision.to_a

    # One query for every decision, keyed the same way the drafts are.
    @approvals = OutreachApproval
                 .where(source: @drafts.map(&:source), job_id: @drafts.map(&:job_id))
                 .index_by(&:key)

    @decided = @approvals.values.count { |a| a.approved? }
  end

  def create
    draft = OutreachDraft.find_by_key(params[:draft_key])
    return redirect_with("That draft is no longer in the warehouse.") if draft.nil?

    decision = params[:decision].to_s
    unless OutreachApproval::DECISIONS.include?(decision)
      return redirect_with("#{decision.inspect} is not a decision.")
    end

    # `text` is what you actually approved - the draft as written, or your edit of
    # it. The fingerprint is computed over THAT, so an edited approval authorises
    # the edited words and nothing else.
    @approval = OutreachApproval.decide!(draft, decision: decision, text: params[:approved_text])
    @draft = draft

    respond_to do |format|
      format.turbo_stream
      format.html { redirect_to outreach_approvals_path }
    end
  end

  private

  def redirect_with(message)
    redirect_to outreach_approvals_path, alert: message
  end
end
