"""
Record the agent's behaviour as replayable scenarios for the website.

The page at /agent/ plays one of these whenever the live service is unreachable -
deploying, restarting, over budget, or simply not built yet - so a visitor never
arrives at a broken console. They are recordings of the real agent, produced by
running it, not hand-written fixtures.

Two kinds, and the page labels them differently because they are different:

  - The happy path is lifted from an actual run log in agent/logs/. A real posting,
    a real model call, a real draft.
  - The rail scenarios are produced here by running the real loop, the real
    validators and the real verifier against a scripted model and a stub cursor -
    the same harness agent/tests uses. The rails firing are genuine; the model
    misbehaving is staged, because a model cannot be made to hallucinate on demand.

That distinction is carried in the data as `staged`, and the page prints it. A
visitor being shown a safety mechanism has to be able to tell which part was real.

    python agent/record_scenarios.py          # writes site/data/agent_runs.json
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("storage", "outreach", "agent"):
    sys.path.insert(0, str(ROOT / sub))

os.environ.setdefault("SIGNAL_PROFILE", "student")

from events import describe, outcome_note  # noqa: E402

import agent as agent_mod  # noqa: E402

OUT = ROOT / "site" / "data" / "agent_runs.json"

# A posting that exists, for the stub cursor to answer about.
POSTING = ("TD Bank", "Data Engineer Intern/Co-op (Winter 2027)")
JOB_ID = "company_board:workday:R_1509826"

TEMPLATE_DRAFTS = {
    "company": "TD Bank",
    "url": "https://zohaiba365.github.io/Signal/companies/td-bank/",
    "insights": [
        {"tier": "company", "kind": "recent_volume",
         "text": "370 of their 1,204 open roles were posted in the last 30 days",
         "evidence": {"last_30d": 370, "roles": 1204}},
        {"tier": "peer", "kind": "stack_emphasis",
         "text": "they mention Power BI in 10% of their postings, about 5.7x the "
                 "rate across the companies I track",
         "evidence": {"own_share": 0.10, "peer_share": 0.017}},
    ],
    "email": "Subject: TD Bank's hiring, from the data side\n\nHi,\n\n(the "
             "deterministic draft, assembled from the observations above)",
    "connection": "Hi - (the deterministic connection note)",
    "followup": "(the deterministic follow-up)",
}


class StubCursor:
    """Answers the status query. Records writes so a scenario can assert none happened."""

    def __init__(self, status="not_contacted", exists=True):
        self.status, self.exists, self.writes, self._row = status, exists, [], None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("INSERT INTO outreach_tracker"):
            self.writes.append(params)
            self._row = None
        elif "FROM raw_postings r" in flat:
            self._row = ((*POSTING, self.status, None, 88) if self.exists else None)
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class Scripted:
    """A model that says exactly what the scenario needs it to say."""

    def __init__(self, script):
        self.messages, self._script, self.calls = self, list(script), 0

    def create(self, **kw):
        self.calls += 1
        step = self._script[min(self.calls - 1, len(self._script) - 1)]
        if step is None:
            return _Resp("end_turn")
        return _Resp("tool_use", *step)


class _Blk:
    def __init__(self, type_, name=None, input_=None, text=None):
        self.type, self.name, self.input, self.text, self.id = type_, name, input_, text, "t1"


class _Resp:
    def __init__(self, stop, name=None, input_=None):
        self.stop_reason = stop
        self.content = [_Blk("tool_use", name, input_) if stop == "tool_use"
                        else _Blk("text", text="done")]
        self.usage = None


class Context:
    peers: dict = {}
    market: dict = {}
    days = 90
    sender = {"school": "University of Waterloo", "program": "Management Engineering",
              "term": "Winter 2027", "role": "Data Engineer"}


CHECK = ("check_application_status", {"job_id": JOB_ID})
DRAFT = ("draft_outreach_email", {"job_id": JOB_ID, "company": "TD Bank"})
UPDATE = ("update_tracker_status", {"job_id": JOB_ID, "new_status": "email_drafted",
                                    "notes": "drafted"})


def run_scenario(script, status="not_contacted", exists=True, max_steps=8):
    """Drive the real loop with a scripted model, and return its record."""
    import tools as tools_mod

    # drafts_for talks to Postgres; these scenarios are about the rails, and a
    # recording should not need a warehouse to produce.
    tools_mod.drafts_for = lambda *a, **k: dict(TEMPLATE_DRAFTS)
    tools_mod.company_url = lambda c: TEMPLATE_DRAFTS["url"]

    cur = StubCursor(status=status, exists=exists)
    record = agent_mod.process_job(
        cur, {"job_id": JOB_ID, "company": POSTING[0], "title": POSTING[1],
              "fit_score": 88},
        Context(), Scripted(script), "claude-haiku-4-5-20251001",
        "claude-haiku-4-5-20251001", max_steps)
    record["_writes"] = len(cur.writes)
    return record


def as_events(record: dict) -> list[dict]:
    events = [describe(step) for step in record.get("steps", [])]
    events.append({"kind": "done", "note": outcome_note(record),
                   "outcome": record.get("outcome")})
    return events


def from_log() -> dict | None:
    """The happy path, taken from a real run rather than staged."""
    logs = sorted(glob.glob(str(ROOT / "agent" / "logs" / "run_*.json")))
    for path in reversed(logs):
        with open(path) as fh:
            data = json.load(fh)
        if data.get("dry_run"):
            continue
        for job in data["jobs"]:
            if job.get("outcome") == "email_drafted" and job.get("draft_source") == "model":
                return {
                    "id": "real", "staged": False,
                    "label": f"{job['company']} — a real run",
                    "company": job["company"], "title": job["title"],
                    "note": "An actual run from the log, model-written and verified.",
                    "events": as_events(job),
                }
    return None


def main() -> None:
    scenarios = []

    real = from_log()
    if real:
        scenarios.append(real)

    scenarios.append({
        "id": "already_contacted", "staged": False,
        "label": "Already contacted",
        "company": POSTING[0], "title": POSTING[1],
        "note": ("The duplicate rail, against a posting already marked as contacted. "
                 "The refusal is real; the tracker row is a sandbox one."),
        "events": as_events(run_scenario([CHECK, DRAFT, UPDATE, None],
                                         status="email_sent")),
    })

    scenarios.append({
        "id": "unknown_posting", "staged": False,
        "label": "A posting that does not exist",
        "company": POSTING[0], "title": POSTING[1],
        "note": ("A reference to a posting absent from the database. Nothing is "
                 "looked up and nothing is written."),
        "events": as_events(run_scenario([CHECK, DRAFT, UPDATE, None], exists=False)),
    })

    scenarios.append({
        "id": "wrong_company", "staged": True,
        "label": "A company the model invented",
        "company": POSTING[0], "title": POSTING[1],
        "note": ("Staged: the model is scripted to name the wrong employer. The "
                 "validator's refusal is the real part."),
        "events": as_events(run_scenario(
            [CHECK, ("draft_outreach_email", {"job_id": JOB_ID,
                                              "company": "Goldman Sachs"}), None])),
    })

    scenarios.append({
        "id": "step_cap", "staged": True,
        "label": "A model that will not stop",
        "company": POSTING[0], "title": POSTING[1],
        "note": ("Staged: the model is scripted to call the same tool forever. The "
                 "cap of 8 calls per posting is the real part."),
        "events": as_events(run_scenario([CHECK] * 20, max_steps=8)),
    })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"scenarios": scenarios}, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(scenarios)} scenario(s)")
    for s in scenarios:
        kinds = [e.get("status", e["kind"]) for e in s["events"]]
        print(f"  {s['id']:<20} staged={str(s['staged']):<5} {kinds}")


if __name__ == "__main__":
    main()
