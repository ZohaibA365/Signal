"""
Tool definitions and the validation that stands between the model and the database.

The design rule, applied everywhere below: anything that should come from the
database is fetched from the database, and anything the model supplies that could
have come from the database is checked against it and rejected on mismatch. A
model argument is treated as a claim, never as a fact.

Everything here is a pure function of its arguments. No I/O, no API calls, no
cursor - so every safety rail is testable without a database or a key, which is the
only way to be sure the rails work before pointing the agent at real data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ------------------------------------------------------------------ vocabulary

# The states an application can be in. Order matters: it is the progression, and
# "further along than" is decided by position in this tuple.
STATUSES = ("not_contacted", "email_drafted", "email_sent", "responded")

# What THIS AGENT may write, as from -> to. Deliberately one transition.
#
# email_sent and responded are states a person reaches by doing something in the
# world. An agent able to write them could report an email it never sent, and the
# tracker would then be a record of fiction. Marking a send stays manual for the
# same reason there is no send_email tool.
ALLOWED_TRANSITIONS = {("not_contacted", "email_drafted")}

# Statuses at which drafting must not happen again. Anything at email_drafted or
# beyond has already been written for, and drafting again is the duplicate this
# whole mechanism exists to prevent.
ALREADY_DRAFTED = STATUSES[STATUSES.index("email_drafted"):]

# A job_id here is the repo's composite key as one string: "source:job_id", the
# same shape eval/golden_set.json uses. One opaque string rather than a source and
# an id as separate arguments, because two fields give the model two chances to
# hand back a combination that does not exist.
JOB_ID_DESCRIPTION = (
    "The posting's full identifier, exactly as given to you, in the form "
    "'source:job_id' - for example 'company_board:lever:abc123'. Never construct "
    "or guess one."
)

TOOLS = [
    {
        "name": "check_application_status",
        "description": (
            "Look up where a posting stands in the outreach tracker. Call this "
            "FIRST for every posting, before anything else. It returns the current "
            "status and whether drafting is allowed. If eligible_to_draft is false, "
            "do not draft - move on and say why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": JOB_ID_DESCRIPTION}},
            "required": ["job_id"],
        },
    },
    {
        "name": "draft_outreach_email",
        "description": (
            "Write outreach for a posting: a cold email, a LinkedIn connection "
            "note and a follow-up. Only call this after check_application_status "
            "returned eligible_to_draft true. The company argument must match the "
            "company that tool reported, exactly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": JOB_ID_DESCRIPTION},
                "company": {
                    "type": "string",
                    "description": ("The employer name exactly as returned by "
                                    "check_application_status. It is checked against "
                                    "the database and the call is rejected if it "
                                    "differs."),
                },
            },
            "required": ["job_id", "company"],
        },
    },
    {
        "name": "update_tracker_status",
        "description": (
            "Record the outcome for a posting. The only transition you may make is "
            "from not_contacted to email_drafted, and only after a draft succeeded. "
            "You cannot mark anything as sent or responded."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": JOB_ID_DESCRIPTION},
                "new_status": {
                    "type": "string",
                    "enum": ["email_drafted"],
                    "description": "Only email_drafted is available to you.",
                },
                "notes": {
                    "type": "string",
                    "description": "One short line on what happened, for the record.",
                },
            },
            "required": ["job_id", "new_status"],
        },
    },
]

TOOL_NAMES = tuple(t["name"] for t in TOOLS)


# ------------------------------------------------------------------- outcomes

@dataclass
class ToolResult:
    """
    What happened when a tool call was attempted.

    Shaped after quality/expectations.py's Result. `ok` False with `rejected` True
    means the call was refused before touching the database, which is the case the
    log exists to make visible - it is evidence a rail fired, not an error.
    """

    tool: str
    ok: bool
    detail: str
    data: dict = field(default_factory=dict)
    rejected: bool = False


# ------------------------------------------------------------------ validation

def validate_call(tool: str, args: dict) -> list[str]:
    """
    Shape-check one tool call before any database work. Returns what is wrong.

    Shape only: that the tool exists, required arguments are present and are
    strings, and no unexpected argument was invented. Whether the job_id refers to
    a real posting is a database question and is answered in agent/tools.py, which
    is the only place that can answer it truthfully.
    """
    problems: list[str] = []
    spec = next((t for t in TOOLS if t["name"] == tool), None)
    if spec is None:
        return [f"unknown tool {tool!r}"]

    schema = spec["input_schema"]
    for name in schema["required"]:
        value = args.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"missing {name}")
        elif not isinstance(value, str):
            problems.append(f"{name} must be a string, got {type(value).__name__}")

    for name in args:
        if name not in schema["properties"]:
            problems.append(f"unexpected argument {name!r}")

    for name, prop in schema["properties"].items():
        if "enum" in prop and name in args and args[name] not in prop["enum"]:
            problems.append(f"{name} must be one of {prop['enum']}, got {args[name]!r}")

    return problems


def validate_transition(current: str, new: str) -> list[str]:
    """Is this a status change the agent is permitted to make?"""
    problems = []
    if current not in STATUSES:
        problems.append(f"current status {current!r} is not a known status")
    if new not in STATUSES:
        problems.append(f"{new!r} is not a known status")
    elif (current, new) not in ALLOWED_TRANSITIONS:
        problems.append(
            f"the agent may not move a posting from {current!r} to {new!r}; "
            f"allowed: {sorted(ALLOWED_TRANSITIONS)}"
        )
    return problems


def draft_is_allowed(status: str) -> tuple[bool, str | None]:
    """Whether a posting at this status may be drafted for, and why not."""
    if status in ALREADY_DRAFTED:
        return False, f"already at status {status!r}; drafting again would duplicate it"
    if status not in STATUSES:
        return False, f"unknown status {status!r}"
    return True, None


def split_job_id(job_id: str) -> tuple[str, str] | None:
    """
    'company_board:lever:abc123' -> ('company_board', 'lever:abc123').

    Split once only: board job ids contain colons of their own, and splitting on
    every colon would silently truncate them into ids that match nothing.
    """
    if not job_id or ":" not in job_id:
        return None
    source, _, rest = job_id.partition(":")
    if not source.strip() or not rest.strip():
        return None
    return source, rest
