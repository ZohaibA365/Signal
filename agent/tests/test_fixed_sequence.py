"""
The path taken when the model that plans the run is unreachable.

The planner is a dependency like any other and it can fail on its own. When it did,
the whole run died and the visitor was told their run "could not be completed" -
true of the planner, false of the warehouse, the rails, the verifier and the
drafting model, all of which were fine and all of which are what actually produce
the email.

run_fixed_sequence runs the three tools in the only order they make sense. The
question these tests answer is whether it is genuinely the same behaviour with the
planning removed, or a second, weaker path that happens to write an email. A
fallback that skips a rail is worse than no fallback, because it only runs on the
days something is already wrong.

No database and no API: the same fakes the rail tests use.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for sub in ("storage", "outreach", "agent"):
    sys.path.insert(0, str(ROOT / sub))

import tools as tools_mod  # noqa: E402

import agent as agent_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The fakes the rail tests already use, rather than a second set that could drift
# from them. There is no package here - pytest collects these by path - so the
# import is by module name.
from test_safety_rails import (  # noqa: E402
    POSTING,
    TEMPLATE,
    FakeContext,
    FakeCursor,
)

JOB_ID = "company_board:workday:R_1509826"


@pytest.fixture(autouse=True)
def _stub_compose(monkeypatch):
    monkeypatch.setattr(tools_mod, "drafts_for", lambda *a, **k: dict(TEMPLATE))
    monkeypatch.setattr(tools_mod, "company_url", lambda c: TEMPLATE["url"])


def job() -> dict:
    return {"job_id": JOB_ID, "company": POSTING["company"],
            "title": POSTING["title"], "fit_score": POSTING["fit_score"]}


def run(cur, client=None, on_event=None) -> dict:
    return agent_mod.run_fixed_sequence(
        cur, job(), FakeContext(), client, "claude-haiku-4-5-20251001",
        on_event=on_event)


def test_it_drafts_and_records_without_any_planner():
    cur = FakeCursor(status="not_contacted")
    record = run(cur)

    assert [s["tool"] for s in record["steps"]] == [
        "check_application_status", "draft_outreach_email", "update_tracker_status"]
    assert record["drafted"] is True
    assert record["outcome"] == "email_drafted"
    assert cur.writes, "the tracker row was never written"


def test_the_duplicate_rail_still_stops_it():
    """
    The rail that matters most, on the path taken when something is already wrong.

    A fallback that drafts for a company already contacted would turn an outage
    into a second message to the same person.
    """
    cur = FakeCursor(status="email_sent")
    record = run(cur)

    assert [s["tool"] for s in record["steps"]] == ["check_application_status"]
    assert record["drafted"] is False
    assert not cur.writes, "it wrote to the tracker for an already-contacted company"


def test_a_posting_that_does_not_exist_is_not_drafted_for():
    cur = FakeCursor(exists=False)
    record = run(cur)

    assert record["drafted"] is False
    assert not cur.writes


def test_every_step_is_streamed_in_order():
    """The console shows the same thing it shows on a normal run."""
    seen = []
    cur = FakeCursor(status="not_contacted")
    run(cur, on_event=seen.append)

    tools = [e["tool"] for e in seen if e.get("tool")]
    assert tools == ["check_application_status", "draft_outreach_email",
                     "update_tracker_status"]
    assert any(e.get("kind") == "outcome" for e in seen), "no terminal event"


def test_a_broken_listener_cannot_take_the_run_down():
    """A visitor closing the tab mid-run is normal and is not the agent's problem."""
    def hostile(_event):
        raise RuntimeError("the browser went away")

    cur = FakeCursor(status="not_contacted")
    record = run(cur, on_event=hostile)
    assert record["drafted"] is True


def test_it_takes_the_same_steps_the_planner_would():
    """
    The fallback and the normal path agree, which is what makes it a fallback
    rather than a second behaviour. Compared as tool sequences against the stub
    client, which walks the path the real model is asked to walk.
    """
    planned = agent_mod.process_job(
        FakeCursor(status="not_contacted"), job(), FakeContext(),
        agent_mod.StubClient(), "claude-haiku-4-5-20251001",
        "claude-haiku-4-5-20251001", agent_mod.MAX_STEPS_PER_JOB)
    fixed = run(FakeCursor(status="not_contacted"))

    assert [s["tool"] for s in fixed["steps"]] == [s["tool"] for s in planned["steps"]]
    assert fixed["drafted"] == planned["drafted"]
