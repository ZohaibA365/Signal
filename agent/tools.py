"""
The three tools, and the database checks that stand behind them.

agent/schemas.py checks the SHAPE of a call - required arguments, types, no
invented arguments. This module checks the TRUTH of one: that the posting exists,
that the company the model named is the company on file, that the status change is
one the agent may make. Shape can be checked without a database; truth cannot.

Every function takes a cursor and returns a ToolResult. None of them raise on bad
input: a rejected call is a normal outcome that gets logged and fed back to the
model, not an exception that ends a batch.
"""

from __future__ import annotations

import os
import sys

# Before importing compose, which reads the profile at import time.
os.environ.setdefault("SIGNAL_PROFILE", "student")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("storage", "outreach", "agent"):
    sys.path.insert(0, os.path.join(_ROOT, _sub))

from compose import _sender, baseline_companies, company_url, drafts_for  # noqa: E402
from insights import collection_days, load_market, peer_stats  # noqa: E402
from schemas import (  # noqa: E402
    ToolResult,
    draft_is_allowed,
    split_job_id,
    validate_transition,
)

# TODO: send_email would attach here, and must not become a fourth tool.
#
# Sending is the one action in this system that cannot be undone, and the agent
# has no way to judge whether a draft is good enough to put a person's name on. So
# the shape to keep is: this module gains a send_email(cur, job_id) that refuses
# unless the row is already at 'email_drafted' AND carries an explicit human
# approval marker written by a separate command a person runs. The model never
# gets the tool in its list; the human calls it. That way the worst an agent
# failure can do is waste a draft.


# ------------------------------------------------------------------ the context

class DraftingContext:
    """
    The per-batch facts drafting needs, computed once.

    compose.py:228-235 is emphatic about why: peer_stats derives its comparison set
    from the list it is handed, so asking for one company yields no peer tier at
    all - and the peer comparison is the strongest line in the message. Databricks
    read "220 of 997 roles posted recently" instead of "Apache Spark in 17% of
    their postings, 3.1x comparable companies", which is the sentence that earns a
    reply. So the baseline is built from the whole corpus, once, and every draft in
    the batch shares it.
    """

    def __init__(self, cur, companies: list[str]):
        self.market = load_market(cur)
        self.peers = peer_stats(cur, baseline_companies(cur, companies))
        self.days = collection_days(cur)
        self.sender = _sender()


# ---------------------------------------------------------------------- lookup

STATUS_SQL = """
    SELECT r.company_name,
           r.job_title,
           coalesce(t.status, 'not_contacted') AS status,
           t.updated_at,
           e.fit_score
    FROM raw_postings r
    LEFT JOIN outreach_tracker t ON t.source = r.source AND t.job_id = r.job_id
    LEFT JOIN job_enrichment  e ON e.source = r.source AND e.job_id = r.job_id
    WHERE r.source = %s AND r.job_id = %s
    LIMIT 1
"""


def check_application_status(cur, job_id: str) -> ToolResult:
    """
    Where a posting stands, and whether it may be drafted for.

    A posting absent from raw_postings is the hallucination case: the model has
    produced an id that refers to nothing, and the honest answer is to say so
    rather than to create a tracker row for a job that does not exist.
    """
    parts = split_job_id(job_id)
    if parts is None:
        return ToolResult("check_application_status", False, rejected=True,
                          detail=f"job_id {job_id!r} is not in the form 'source:job_id'",
                          data={"job_id": job_id, "exists": False})

    cur.execute(STATUS_SQL, parts)
    row = cur.fetchone()
    if row is None:
        return ToolResult("check_application_status", False, rejected=True,
                          detail=f"no posting {job_id!r} exists",
                          data={"job_id": job_id, "exists": False})

    company, title, status, updated_at, fit_score = row
    allowed, reason = draft_is_allowed(status)
    return ToolResult("check_application_status", True,
                      detail=f"{status}; drafting {'allowed' if allowed else 'blocked'}",
                      data={
                          "job_id": job_id, "exists": True, "status": status,
                          "company": company, "job_title": title,
                          "fit_score": fit_score,
                          "eligible_to_draft": allowed, "reason": reason,
                          "updated_at": updated_at.isoformat() if updated_at else None,
                      })


# --------------------------------------------------------------------- drafting

def draft_outreach_email(cur, job_id: str, company: str, context: DraftingContext,
                         client=None, model: str = "") -> ToolResult:
    """
    Draft the three variants for a posting, model-written where it verifies.

    Two rails fire before any drafting. The posting must exist, and the company the
    model passed must be the company on file for it - a model that supplies a
    company from somewhere other than check_application_status is exactly the case
    "never trust a model-generated value that should have come from the database"
    is about, and the mismatch is rejected rather than quietly corrected.
    """
    status = check_application_status(cur, job_id)
    if not status.data.get("exists"):
        return ToolResult("draft_outreach_email", False, rejected=True,
                          detail=status.detail, data={"job_id": job_id})

    stored_company = status.data["company"]
    if (company or "").strip().lower() != (stored_company or "").strip().lower():
        return ToolResult(
            "draft_outreach_email", False, rejected=True,
            detail=(f"company mismatch: the model passed {company!r} but "
                    f"{job_id} belongs to {stored_company!r}"),
            data={"job_id": job_id, "passed": company, "stored": stored_company})

    # The duplicate rail, enforced here as well as in check_application_status.
    # Enforcing it only there would make it advice: the model would simply have to
    # skip the check to get around it.
    if not status.data["eligible_to_draft"]:
        return ToolResult("draft_outreach_email", False, rejected=True,
                          detail=status.data["reason"], data={"job_id": job_id})

    template = drafts_for(cur, stored_company, context.peers, context.market, context.days)
    if template.get("error"):
        return ToolResult("draft_outreach_email", False,
                          detail=f"cannot draft for {stored_company}: {template['error']}",
                          data={"job_id": job_id, "company": stored_company})

    from draft import drafts_with_fallback

    drafts, source, verification, usage = drafts_with_fallback(
        client, template, context.sender, model)

    return ToolResult(
        "draft_outreach_email", True,
        detail=f"{len(template['insights'])} insight(s), drafted by the {source}",
        data={
            "job_id": job_id, "company": stored_company,
            "url": company_url(stored_company),
            "insight_count": len(template["insights"]),
            "source": source, "verification": verification, "drafts": drafts,
            "usage": {"input": getattr(usage, "input_tokens", 0),
                      "output": getattr(usage, "output_tokens", 0)} if usage else None,
        })


# ---------------------------------------------------------------------- writing

UPSERT = """
INSERT INTO outreach_tracker (
    source, job_id, company_name, job_title, status,
    draft_email, draft_connection, draft_followup, draft_source, notes, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (source, job_id) DO UPDATE SET
    status           = EXCLUDED.status,
    draft_email      = coalesce(EXCLUDED.draft_email,      outreach_tracker.draft_email),
    draft_connection = coalesce(EXCLUDED.draft_connection, outreach_tracker.draft_connection),
    draft_followup   = coalesce(EXCLUDED.draft_followup,   outreach_tracker.draft_followup),
    draft_source     = coalesce(EXCLUDED.draft_source,     outreach_tracker.draft_source),
    notes            = EXCLUDED.notes,
    updated_at       = NOW()
"""


def update_tracker_status(cur, job_id: str, new_status: str, notes: str = "",
                          drafts: dict | None = None, draft_source: str | None = None
                          ) -> ToolResult:
    """
    Move a posting to a new status, if the agent is allowed to make that move.

    The transition check is the rail that matters. Only not_contacted ->
    email_drafted is permitted: an agent that could write email_sent could report
    an email it never sent, and the tracker would become a record of fiction.
    """
    status = check_application_status(cur, job_id)
    if not status.data.get("exists"):
        return ToolResult("update_tracker_status", False, rejected=True,
                          detail=status.detail, data={"job_id": job_id})

    current = status.data["status"]
    problems = validate_transition(current, new_status)

    # A status is a claim, and 'email_drafted' claims a draft exists. Without this
    # the tracker will happily say a posting was drafted for while its draft
    # columns are null - which the first dry run did, because the model called
    # update after its draft call had been rejected. The status must be earned by
    # the thing it describes.
    if not problems and new_status == "email_drafted" and not (drafts or {}).get("email"):
        problems.append("no draft was produced for this posting, so it cannot be "
                        "recorded as email_drafted")

    if problems:
        return ToolResult("update_tracker_status", False, rejected=True,
                          detail="; ".join(problems),
                          data={"job_id": job_id, "current": current,
                                "attempted": new_status})

    source, real_id = split_job_id(job_id)
    drafts = drafts or {}
    cur.execute(UPSERT, (
        source, real_id, status.data["company"], status.data["job_title"], new_status,
        drafts.get("email"), drafts.get("connection"), drafts.get("followup"),
        draft_source, notes or None,
    ))
    return ToolResult("update_tracker_status", True,
                      detail=f"{current} -> {new_status}",
                      data={"job_id": job_id, "previous_status": current,
                            "new_status": new_status})
