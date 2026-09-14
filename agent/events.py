"""
Turn a tool call into a sentence a non-technical visitor understands.

The agent already records everything it does: tool, arguments, result, whether the
call was rejected. That record is precise and unreadable unless you know the
codebase. This is the one place that translates it, and it is deliberately the ONLY
place - the recorded scenarios on the site and the live service both read from
here, so the wording cannot drift between what a visitor sees in a replay and what
they see in a live run.

It adds nothing and decides nothing. Every field it reads was already in the log.
"""

from __future__ import annotations

# Said before a tool runs, so the console reads as narration rather than a dump.
BEFORE = {
    "check_application_status": "Checking whether this company has been contacted before...",
    "draft_outreach_email": "Drafting outreach from what the warehouse knows about them...",
    "update_tracker_status": "Recording the outcome in the tracker...",
}

# Said after. Keyed on the tool and whether the call was allowed to proceed. The
# rejected wordings matter most: they are the moments the page exists to show, and
# they have to read as the system working rather than the system erroring.
AFTER_OK = {
    "check_application_status": lambda d: (
        "Not contacted before — clear to draft."
        if d.get("eligible_to_draft")
        else f"Already contacted ({d.get('status')}) — skipping to avoid a duplicate."
    ),
    "draft_outreach_email": lambda d: (
        f"Drafted from {d.get('insight_count', 0)} verified observation(s), "
        f"written by the {d.get('source', 'template')}."
    ),
    "update_tracker_status": lambda d: (
        f"Tracker updated: {d.get('previous_status')} → {d.get('new_status')}."
    ),
}

def rejected_note(tool: str, data: dict, detail: str) -> str:
    """
    Why a call was refused, in the visitor's language.

    Specific rather than generic, because "refused" alone reads like an error while
    "the model named Goldman Sachs but this posting belongs to TD Bank" reads like
    the system working - and the second one is what actually happened.
    """
    if data.get("exists") is False or (detail or "").startswith("no posting"):
        return "That posting is not in the database. Nothing was looked up."
    if tool == "draft_outreach_email":
        if data.get("passed") and data.get("stored"):
            return (f"Refused: the model said {data['passed']}, but this posting "
                    f"belongs to {data['stored']}. Nothing was drafted.")
        if "already at status" in (detail or ""):
            return "Refused: already contacted. Nothing was drafted."
        return f"Refused. {detail}" if detail else "Refused — nothing was drafted."
    if tool == "update_tracker_status":
        current, attempted = data.get("current"), data.get("attempted")
        if current and attempted:
            return (f"Refused: this posting is already {current}, and the agent may "
                    f"only move one from not_contacted to email_drafted. The tracker "
                    f"was not changed.")
        if attempted:
            return (f"Refused: the agent may not set {attempted}. The tracker was "
                    f"not changed.")
        return "Refused — the tracker was not changed."
    return f"Refused. {detail}" if detail else "Refused."


def describe(step: dict) -> dict:
    """
    One log entry in, one display event out.

    `step` is exactly what agent.py puts in its run log, so this works equally on a
    recording read from disk and on a live callback.
    """
    tool = step.get("tool", "")
    data = step.get("data") or {}

    if step.get("rejected"):
        status = "rejected"
        note = rejected_note(tool, data, step.get("detail", ""))
    elif not step.get("ok"):
        status, note = "failed", "That step failed. The run continues to the next posting."
    else:
        status = "ok"
        note = AFTER_OK.get(tool, lambda _: "Done.")(data)

    return {
        "kind": "step",
        "tool": tool,
        "status": status,
        "before": BEFORE.get(tool, ""),
        "note": note,
        # The engine's own words, shown smaller. Kept because a technical visitor
        # will want to see that the sentence above is a translation of something
        # real rather than a caption.
        "detail": step.get("detail", ""),
        "arguments": step.get("arguments") or {},
        "ms": step.get("ms", 0),
    }


def outcome_note(record: dict) -> str:
    """The closing line for one posting."""
    outcome = record.get("outcome")
    if outcome == "email_drafted":
        source = record.get("draft_source", "template")
        return (f"Done. Draft written by the {source}, "
                f"every figure in it checked against the warehouse.")
    if outcome == "step limit reached":
        return "Stopped at the step limit. The agent is capped at 8 calls per posting."
    if outcome == "finished":
        return "Finished without drafting."
    return f"Finished: {outcome}."
