"""
The agent loop: walk high-scoring postings, draft outreach, record the outcome.

One job at a time, sequentially. Per job the model is given the three tools and the
posting, and the expected path is three calls - check, draft, update. The model
chooses; the code decides whether each choice is allowed.

Every rail is enforced here or in agent/tools.py, never by instruction alone. The
prompt asks the model to check status first, and the draft tool independently
refuses if the posting is already drafted, so a model that ignores the instruction
gets the same answer. The posture throughout is that the model may be wrong or
adversarial, and the system should be uninteresting either way.

Nothing sends. The only status this can write is 'email_drafted'.

    python agent/agent.py --dry-run --limit 3     # stub model, no API, no writes
    python agent/agent.py --limit 1               # one real posting, end to end
    python agent/agent.py --job-id company_board:lever:abc123
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
for sub in ("storage", "outreach", "agent"):
    sys.path.insert(0, str(ROOT / sub))

os.environ.setdefault("SIGNAL_PROFILE", "student")

from db import connect  # noqa: E402
from schemas import TOOLS, ToolResult, validate_call  # noqa: E402
from tools import (  # noqa: E402
    DraftingContext,
    check_application_status,
    draft_outreach_email,
    update_tracker_status,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("agent")

LOGS_DIR = ROOT / "agent" / "logs"
RUN_STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

# Tool calls per JOB, not per batch.
#
# Eight because the happy path is three - check, draft, update - and the cap should
# leave room for a retry and a re-check while still stopping a loop that has gone
# wrong. A cap across the whole batch would be the wrong shape entirely: at three
# calls a job, a run of thirty would die on the second one.
MAX_STEPS_PER_JOB = 8

# Haiku for orchestration: this is choosing among three tools and passing an id
# through, which is the cheapest thing a model can be asked to do. Drafting is
# switched separately, since prose is the one place a stronger model reads better -
# and it is not a safety tradeoff, because the verifier applies to both.
MODEL = "claude-haiku-4-5-20251001"
DRAFT_MODEL = "claude-haiku-4-5-20251001"

BATCH_SQL = """
    SELECT a.source || ':' || a.job_id AS job_id,
           a.company_name, a.job_title, a.fit_score
    FROM apply_queue a
    LEFT JOIN outreach_tracker t
           ON t.source = a.source AND t.job_id = a.job_id
    WHERE a.fit_score >= %(min_score)s
      AND a.link_tier = 'direct'
      AND coalesce(t.status, 'not_contacted') = %(status)s
    ORDER BY a.fit_score DESC, a.company_name, a.job_id
    LIMIT %(limit)s
"""

SYSTEM_PROMPT = """You process one job posting at a time for a student doing
outreach about internships.

For the posting you are given:

1. Call check_application_status first, always.
2. If it returns eligible_to_draft false, stop. Do not draft. Say in one sentence
   why you stopped.
3. If it returns true, call draft_outreach_email, passing the company exactly as
   that tool reported it.
4. If the draft succeeds, call update_tracker_status with new_status
   "email_drafted" and a one-line note.
5. Then stop and summarise in one sentence.

Never invent a job_id or a company name. Every value you pass must have come from a
tool result you have already seen. You cannot send anything; drafting is where this
ends."""


def fetch_batch(cur, min_score: int, status: str, limit: int) -> list[dict]:
    cur.execute(BATCH_SQL, {"min_score": min_score, "status": status, "limit": limit})
    return [{"job_id": r[0], "company": r[1], "title": r[2], "fit_score": r[3]}
            for r in cur.fetchall()]


class StubClient:
    """
    A model that behaves, for --dry-run.

    Walks the expected path so the loop, the validation, the logging and the
    writing can all be exercised with no API call and no key. It is not a test
    double for the rails - agent/tests does that with misbehaving scripts.
    """

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        # State is derived from the conversation rather than kept on the object.
        # A counter on the instance leaked across jobs in the first version: job
        # two started at step four and immediately ended, so two of three postings
        # were silently skipped while the run reported success.
        first = kw["messages"][0]["content"]
        job_id = first.split("job_id: ")[1].split("\n")[0].strip()

        results = []
        for message in kw["messages"]:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    # tool_result blocks are plain dicts on the way back to the
                    # model, and Anthropic objects on the way out. Reading them
                    # with getattr found nothing, so the stub passed an empty
                    # company and its own draft call was rejected.
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results.append(json.loads(block["content"]))

        if not results:
            return _Stub("tool_use", "check_application_status", {"job_id": job_id})

        status = results[0]
        if not status.get("eligible_to_draft", False):
            return _Stub("end_turn", text=f"skipping: {status.get('reason')}")
        if len(results) == 1:
            return _Stub("tool_use", "draft_outreach_email",
                         {"job_id": job_id, "company": status.get("company")})
        if len(results) == 2:
            if not results[1].get("drafts"):
                return _Stub("end_turn", text="drafting failed, not recording anything")
            return _Stub("tool_use", "update_tracker_status",
                         {"job_id": job_id, "new_status": "email_drafted",
                          "notes": f"drafted by the {results[1].get('source')}"})
        return _Stub("end_turn", text="done")


class _Block:
    def __init__(self, type_, name=None, input_=None, text=None, id_="stub"):
        self.type, self.name, self.input, self.text, self.id = type_, name, input_, text, id_


class _Stub:
    def __init__(self, stop_reason, name=None, input_=None, text=None):
        self.stop_reason = "tool_use" if stop_reason == "tool_use" else "end_turn"
        self.content = [_Block("tool_use", name, input_) if stop_reason == "tool_use"
                        else _Block("text", text=text)]
        self.usage = None


def execute(cur, tool: str, args: dict, context: DraftingContext, client, draft_model,
            last_draft: dict | None = None) -> ToolResult:
    """
    Run one tool call, shape-checked first, and never raise.

    A tool that raised would end the batch on one bad posting. Every failure here
    becomes a ToolResult the model can read and the log can record, which is what
    "no silent failures" has to mean in practice: loud, recorded, and survivable.
    """
    problems = validate_call(tool, args)
    if problems:
        return ToolResult(tool, False, rejected=True,
                          detail="; ".join(problems), data={"args": args})
    try:
        if tool == "check_application_status":
            return check_application_status(cur, args["job_id"])
        if tool == "draft_outreach_email":
            return draft_outreach_email(cur, args["job_id"], args["company"],
                                        context, client, draft_model)
        if tool == "update_tracker_status":
            # The drafts travel with the status change rather than in a second
            # write afterwards. Two writes meant the row briefly claimed a draft
            # it did not yet have, and the rail that now checks for one would have
            # rejected the first of them.
            last = last_draft or {}
            return update_tracker_status(cur, args["job_id"], args["new_status"],
                                         args.get("notes", ""),
                                         drafts=last.get("drafts"),
                                         draft_source=last.get("source"))
        return ToolResult(tool, False, rejected=True, detail=f"unknown tool {tool!r}")
    except Exception as exc:                                        # noqa: BLE001
        return ToolResult(tool, False, detail=f"{type(exc).__name__}: {exc}"[:300],
                          data={"args": args})


def process_job(cur, job: dict, context: DraftingContext, client, model: str,
                draft_model: str, max_steps: int) -> dict:
    """One posting through the loop. Returns the record for the run log."""
    record = {"job_id": job["job_id"], "company": job["company"],
              "title": job["title"], "fit_score": job["fit_score"],
              "steps": [], "outcome": None, "drafted": False}

    messages = [{"role": "user", "content":
                 f"job_id: {job['job_id']}\ncompany (for reference only, verify it): "
                 f"{job['company']}\ntitle: {job['title']}"}]
    last_draft: dict = {}

    for step in range(1, max_steps + 1):
        response = client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM_PROMPT,
            tools=TOOLS, messages=messages)

        if response.stop_reason != "tool_use":
            said = next((b.text for b in response.content
                         if getattr(b, "type", "") == "text"), "")
            record["outcome"] = record["outcome"] or "finished"
            record["summary"] = (said or "").strip()[:300]
            return record

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            started = time.time()
            result = execute(cur, block.name, dict(block.input or {}),
                             context, client, draft_model, last_draft)
            entry = {
                "step": step, "tool": block.name, "arguments": dict(block.input or {}),
                "ok": result.ok, "rejected": result.rejected, "detail": result.detail,
                "ms": int((time.time() - started) * 1000),
                "at": datetime.now(UTC).isoformat(),
                # The drafts themselves are stored on the tracker row; keeping the
                # full text in every log entry as well would triple the file.
                "data": {k: v for k, v in result.data.items() if k != "drafts"},
            }
            record["steps"].append(entry)
            level = log.info if result.ok else log.warning
            level("    %-26s %-9s %s", block.name,
                  "ok" if result.ok else ("rejected" if result.rejected else "failed"),
                  result.detail)

            if block.name == "draft_outreach_email" and result.ok:
                last_draft = result.data
            if block.name == "update_tracker_status" and result.ok:
                record["drafted"] = True
                record["outcome"] = "email_drafted"
                record["draft_source"] = last_draft.get("source")
                record["verification"] = last_draft.get("verification")

            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(result.data, default=str)})
        messages.append({"role": "user", "content": results})

    # The cap. Reported as its own outcome rather than as an error, because hitting
    # it means the model kept going, which is information about the model.
    record["outcome"] = "step limit reached"
    log.warning("    step limit reached after %s calls", max_steps)
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description="Draft outreach for high-scoring postings")
    ap.add_argument("--limit", type=int, default=5, help="how many postings (default 5)")
    ap.add_argument("--min-score", type=int, default=75)
    ap.add_argument("--status", default="not_contacted")
    ap.add_argument("--job-id", help="one specific posting, ignoring the batch query")
    ap.add_argument("--dry-run", action="store_true",
                    help="stub model, no API call; database writes still happen")
    ap.add_argument("--no-write", action="store_true", help="roll back every write")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--draft-model", default=DRAFT_MODEL)
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS_PER_JOB)
    args = ap.parse_args()

    conn = connect()
    try:
        with conn.cursor() as cur:
            if args.job_id:
                probe = check_application_status(cur, args.job_id)
                if not probe.data.get("exists"):
                    raise SystemExit(probe.detail)
                batch = [{"job_id": args.job_id, "company": probe.data["company"],
                          "title": probe.data["job_title"],
                          "fit_score": probe.data["fit_score"]}]
            else:
                batch = fetch_batch(cur, args.min_score, args.status, args.limit)

            if not batch:
                log.info("nothing to do: no postings at %s with a score of %s or more",
                         args.status, args.min_score)
                return

            companies = sorted({j["company"] for j in batch if j["company"]})
            log.info("%s posting(s) across %s companies", len(batch), len(companies))
            if len(companies) < len(batch):
                # Said out loud because the email is company-level: several
                # postings at one employer produce near-identical drafts, and
                # whoever approves the sends needs to see that before sending five
                # of them to the same person.
                log.warning("the batch repeats companies, so drafts will be "
                            "near-identical within each one")

            context = DraftingContext(cur, companies)
            client = StubClient() if args.dry_run else _real_client()

            records = []
            for i, job in enumerate(batch, 1):
                log.info("[%s/%s] %s - %s", i, len(batch), job["company"], job["title"])
                try:
                    records.append(process_job(cur, job, context, client, args.model,
                                               args.draft_model, args.max_steps))
                except Exception as exc:                            # noqa: BLE001
                    # One posting must never take the batch with it.
                    log.error("    unhandled: %s: %s", type(exc).__name__, exc)
                    records.append({"job_id": job["job_id"], "outcome": "error",
                                    "error": f"{type(exc).__name__}: {exc}"[:300],
                                    "steps": []})

            if args.no_write:
                conn.rollback()
                log.info("--no-write: rolled back")
            else:
                conn.commit()
            _write_log(records, args, companies)
    finally:
        conn.close()


def _real_client():
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(timeout=90.0, max_retries=3)


def _write_log(records: list[dict], args, companies: list[str]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    drafted = [r for r in records if r.get("drafted")]
    by_model = [r for r in drafted if r.get("draft_source") == "model"]
    payload = {
        "run": RUN_STAMP,
        "finished_at": datetime.now(UTC).isoformat(),
        "model": args.model, "draft_model": args.draft_model,
        "dry_run": args.dry_run,
        "criteria": {"min_score": args.min_score, "status": args.status,
                     "limit": args.limit, "job_id": args.job_id},
        "max_steps_per_job": args.max_steps,
        "summary": {
            "postings": len(records), "companies": len(companies),
            "drafted": len(drafted),
            "drafted_by_model": len(by_model),
            "drafted_by_template": len(drafted) - len(by_model),
            "rejected_calls": sum(1 for r in records
                                  for s in r.get("steps", []) if s.get("rejected")),
            "outcomes": {o: sum(1 for r in records if r.get("outcome") == o)
                         for o in {r.get("outcome") for r in records}},
        },
        "jobs": records,
    }
    path = LOGS_DIR / f"run_{RUN_STAMP}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
    s = payload["summary"]
    log.info("%s drafted (%s by the model, %s by the template), %s rejected call(s)",
             s["drafted"], s["drafted_by_model"], s["drafted_by_template"],
             s["rejected_calls"])
    log.info("wrote %s", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
