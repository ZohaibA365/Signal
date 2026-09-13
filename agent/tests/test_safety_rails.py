"""
The safety rails, exercised against a scripted misbehaving model.

These are the tests that matter in this directory. The agent writes to the tracker
and drafts messages a person may put their name on, so the question is not whether
it works when the model behaves - it is what happens when the model hallucinates an
id, invents a statistic, tries to mark something sent, or simply never stops.

No database and no API. A fake cursor answers the two queries the tools issue, and
a fake client returns whatever sequence of tool calls a scenario needs. Every rail
is therefore provable in milliseconds, which is the only way to be sure they hold
before pointing this at real postings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for sub in ("storage", "outreach", "agent"):
    sys.path.insert(0, str(ROOT / sub))

import schemas  # noqa: E402
import tools as tools_mod  # noqa: E402

import agent as agent_mod  # noqa: E402

# One posting that exists, at a status each scenario sets.
POSTING = {"company": "TD Bank", "title": "Data Engineer Intern (Winter 2027)",
           "fit_score": 88}


class FakeCursor:
    """Answers the status query; records every write."""

    def __init__(self, status="not_contacted", exists=True):
        self.status, self.exists = status, exists
        self.writes: list[tuple] = []
        self._result = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("INSERT INTO outreach_tracker"):
            self.writes.append(params)
            self._result = None
        elif "FROM raw_postings r" in flat:
            self._result = ((POSTING["company"], POSTING["title"], self.status,
                             None, POSTING["fit_score"]) if self.exists else None)
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []


class ScriptedClient:
    """A model that says exactly what a scenario tells it to."""

    def __init__(self, script):
        self.messages = self
        self._script = list(script)
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        step = self._script[min(self.calls - 1, len(self._script) - 1)]
        if step is None:
            return _Resp("end_turn", text="done")
        name, args = step
        return _Resp("tool_use", name, args)


class _Blk:
    def __init__(self, type_, name=None, input_=None, text=None):
        self.type, self.name, self.input, self.text, self.id = type_, name, input_, text, "t1"


class _Resp:
    def __init__(self, stop, name=None, input_=None, text=None):
        self.stop_reason = stop
        self.content = [_Blk("tool_use", name, input_) if stop == "tool_use"
                        else _Blk("text", text=text)]
        self.usage = None


class FakeContext:
    """Drafting context without touching the database or compose.py."""

    peers: dict = {}
    market: dict = {}
    days = 90
    sender = {"school": "Waterloo", "program": "Management Engineering",
              "term": "Winter 2027", "role": "Data Engineering"}


TEMPLATE = {
    "company": "TD Bank",
    "url": "https://zohaiba365.github.io/Signal/companies/td-bank/",
    "insights": [{"tier": "company", "kind": "recent_volume",
                  "text": "12 of their 40 open roles were posted in the last 30 days",
                  "evidence": {"last_30d": 12, "roles": 40}}],
    "email": "Subject: TD Bank's hiring, from the data side\n\nHi,\n\ntemplate email",
    "connection": "Hi - template note",
    "followup": "template follow-up",
}


@pytest.fixture(autouse=True)
def _stub_compose(monkeypatch):
    """drafts_for talks to Postgres; the scenarios are about the rails, not SQL."""
    monkeypatch.setattr(tools_mod, "drafts_for", lambda *a, **k: dict(TEMPLATE))
    monkeypatch.setattr(tools_mod, "company_url", lambda c: TEMPLATE["url"])


def run(script, status="not_contacted", exists=True, max_steps=8, client=None):
    cur = FakeCursor(status=status, exists=exists)
    client = client or ScriptedClient(script)
    record = agent_mod.process_job(
        cur, {"job_id": "company_board:lever:abc", "company": POSTING["company"],
              "title": POSTING["title"], "fit_score": 88},
        FakeContext(), client, "m", "m", max_steps)
    return record, cur


HAPPY = [("check_application_status", {"job_id": "company_board:lever:abc"}),
         ("draft_outreach_email", {"job_id": "company_board:lever:abc",
                                   "company": "TD Bank"}),
         ("update_tracker_status", {"job_id": "company_board:lever:abc",
                                    "new_status": "email_drafted", "notes": "ok"}),
         None]


def test_1_already_sent_is_skipped_not_redrafted():
    """The duplicate rail. A posting already contacted must not be drafted again."""
    record, cur = run(HAPPY, status="email_sent")

    check = record["steps"][0]
    assert check["data"]["eligible_to_draft"] is False
    assert "already at status 'email_sent'" in check["data"]["reason"]

    # And the rail holds even when the model ignores that and drafts anyway.
    draft = record["steps"][1]
    assert draft["tool"] == "draft_outreach_email"
    assert draft["rejected"] is True
    assert cur.writes == [], "nothing may be written for an already-contacted posting"


def test_2_a_hallucinated_job_id_is_rejected():
    """A posting that does not exist gets no tracker row invented for it."""
    record, cur = run(HAPPY, exists=False)
    assert record["steps"][0]["data"]["exists"] is False
    assert all(s["rejected"] for s in record["steps"]), record["steps"]
    assert cur.writes == []


def test_3_a_normal_job_drafts_and_updates():
    record, cur = run(HAPPY)
    tools_used = [s["tool"] for s in record["steps"]]
    assert tools_used == ["check_application_status", "draft_outreach_email",
                          "update_tracker_status"]
    assert all(s["ok"] for s in record["steps"])
    assert record["drafted"] is True
    assert record["outcome"] == "email_drafted"
    # The drafts reach the row, with who wrote them.
    assert cur.writes, "the tracker row must be written"
    written = cur.writes[-1]
    assert written[4] == "email_drafted"
    assert "template email" in written[5]


def test_4_a_model_that_never_stops_hits_the_cap():
    """Eight calls per job, then stop cleanly and say so."""
    forever = [("check_application_status", {"job_id": "company_board:lever:abc"})]
    record, _ = run(forever * 20, max_steps=8)
    assert record["outcome"] == "step limit reached"
    assert len(record["steps"]) == 8, "the cap is a count of tool calls"


def test_5_a_company_the_model_invented_is_rejected():
    """The 'never trust a model value that should come from the DB' rail."""
    script = [HAPPY[0],
              ("draft_outreach_email", {"job_id": "company_board:lever:abc",
                                        "company": "Goldman Sachs"}),
              None]
    record, cur = run(script)
    draft = record["steps"][1]
    assert draft["rejected"] is True
    assert "company mismatch" in draft["detail"]
    assert "Goldman Sachs" in draft["detail"] and "TD Bank" in draft["detail"]
    assert cur.writes == []


def test_6_the_agent_cannot_mark_something_sent():
    """
    email_sent is a claim about the world, so the agent may not write it.

    Two independent rails refuse it and the outer one fires first: the tool schema
    only offers email_drafted, so the call is rejected on its shape before any
    database work. That ordering is worth asserting explicitly, and so is the inner
    rail underneath it - if the enum were ever widened, validate_transition still
    refuses, which is what defence in depth has to mean here.
    """
    script = [HAPPY[0], HAPPY[1],
              ("update_tracker_status", {"job_id": "company_board:lever:abc",
                                         "new_status": "email_sent", "notes": "sent"}),
              None]
    record, cur = run(script)
    update = record["steps"][2]
    assert update["rejected"] is True
    assert "must be one of ['email_drafted']" in update["detail"]
    assert cur.writes == [], "a rejected transition must write nothing"

    # The inner rail, reached directly, in case the schema ever stops guarding it.
    assert schemas.validate_transition("not_contacted", "email_sent")
    assert schemas.validate_transition("email_drafted", "email_drafted")
    assert schemas.validate_transition("not_contacted", "email_drafted") == []


def test_6b_drafting_twice_is_refused_by_the_transition_rail():
    """
    The transition rail on its own, on the path that actually reaches it: a posting
    already at email_drafted cannot be moved there again.
    """
    script = [HAPPY[0], HAPPY[2], None]
    record, cur = run(script, status="email_drafted")
    update = record["steps"][1]
    assert update["rejected"] is True
    assert "may not move" in update["detail"]
    assert cur.writes == []


def test_7_a_tool_that_raises_does_not_kill_the_run(monkeypatch):
    """No silent failures, and no loud ones that take the batch down either."""
    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(agent_mod, "draft_outreach_email", boom)
    record, _ = run(HAPPY)
    failed = record["steps"][1]
    assert failed["ok"] is False and failed["rejected"] is False
    assert "RuntimeError: database went away" in failed["detail"]


def test_8_an_unknown_tool_is_refused_before_the_database():
    record, cur = run([("send_email", {"job_id": "company_board:lever:abc"}), None])
    assert record["steps"][0]["rejected"] is True
    assert "unknown tool" in record["steps"][0]["detail"]
    assert cur.writes == []


def test_9_a_malformed_call_is_refused_before_the_database():
    """Shape validation: a missing required argument never reaches SQL."""
    record, cur = run([("draft_outreach_email", {"job_id": "company_board:lever:abc"}),
                       None])
    assert record["steps"][0]["rejected"] is True
    assert "missing company" in record["steps"][0]["detail"]
    assert cur.writes == []


def test_the_log_entry_can_reconstruct_the_run():
    """Every call recorded with enough to answer 'what did it do, and why'."""
    record, _ = run(HAPPY)
    for step in record["steps"]:
        assert {"step", "tool", "arguments", "ok", "rejected", "detail", "at"} <= set(step)
    assert json.dumps(record, default=str)


def test_the_allowed_transition_set_is_exactly_one():
    """If this ever grows, it should be a deliberate decision with a reason."""
    assert schemas.ALLOWED_TRANSITIONS == {("not_contacted", "email_drafted")}


def test_no_send_tool_is_exposed_to_the_model():
    assert "send_email" not in schemas.TOOL_NAMES
    assert len(schemas.TOOLS) == 3
