"""
The public endpoint: one agent run, streamed to a browser as it happens.

Deliberately small. One endpoint that does work, one that reads counters, one that
says whether the process is alive. No background jobs, no scheduler, no queue, no
cache, no state beyond two tables. The things that break are the things that run,
and a page that must never look broken is best served by a service with very little
in it.

Every failure here is the same failure to a visitor. The page gives this two
seconds to answer and falls back to a recorded run on anything else - a timeout, a
502 while deploying, the budget ceiling, a dropped stream halfway through. That is
why this file can afford to be strict about refusing work: refusing is invisible.

    uvicorn service.app:app --reload        # local
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("storage", "outreach", "agent", "ingestion", "service"):
    sys.path.insert(0, os.path.join(ROOT, sub))

os.environ.setdefault("SIGNAL_PROFILE", "student")

import limits  # noqa: E402
import psycopg2  # noqa: E402
import resolve as resolver  # noqa: E402
import sandbox  # noqa: E402
from events import describe, outcome_note  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from tools import DraftingContext  # noqa: E402

import agent as agent_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("service")

app = FastAPI(title="Signal outreach agent", docs_url=None, redoc_url=None)

# The page is served from GitHub Pages, so it is a different origin. Listed
# explicitly rather than "*": this endpoint spends money, and there is no reason
# for any other site to be able to spend it.
ALLOWED = [o for o in os.getenv(
    "AGENT_ALLOWED_ORIGINS",
    "https://zohaiba365.github.io,http://localhost:8000").split(",") if o]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED, allow_methods=["POST", "GET"],
                   allow_headers=["Content-Type"])

MODEL = os.getenv("AGENT_MODEL", "claude-haiku-4-5-20251001")
DRAFT_MODEL = os.getenv("AGENT_DRAFT_MODEL", MODEL)

# A run that has not finished by now never will, from the visitor's point of view.
# Three tool calls and two model calls take about fifteen seconds; this is the
# point at which ending cleanly beats continuing to wait.
RUN_TIMEOUT_S = float(os.getenv("AGENT_RUN_TIMEOUT_S", "45"))


def demo_connection():
    """
    A connection confined to the sandbox.

    Two things make it safe, and neither is a convention. It authenticates as
    signal_demo, which has write access to exactly one table; and search_path puts
    demo first, so the agent's unqualified outreach_tracker resolves there while the
    warehouse still reads from public. See service/sandbox.py.
    """
    url = os.environ["DEMO_DATABASE_URL"]
    conn = psycopg2.connect(url, connect_timeout=15)
    conn.autocommit = True
    with conn.cursor() as cur:
        # SET rather than a connection option. Neon's pooled endpoint rejects the
        # libpq `options` startup parameter outright - "unsupported startup
        # parameter" - so passing statement_timeout that way made every connection
        # fail, and the service reported itself unavailable with a working database
        # behind it.
        cur.execute("SET statement_timeout = 20000")
        cur.execute("SET search_path TO demo, public")
    return conn


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


# What a visitor may tell us about themselves. Short, because these land inside a
# message somebody may send: a long free-text field here is a way to write most of
# somebody else's email through this service.
SENDER_FIELDS = {"name": 60, "program": 80, "school": 80, "term": 40, "role": 60}


def clean_sender(raw: dict | None) -> dict | None:
    """
    The visitor's own details, trimmed and bounded, or None for the default.

    Stripped of newlines and truncated per field. Everything here is interpolated
    into a draft, and the verifier checks the draft's FIGURES rather than its
    prose, so the length limits are the control on what can be injected through it.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for field, cap in SENDER_FIELDS.items():
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = " ".join(value.split())[:cap]
    if not out:
        return None
    # The templates read these five; missing ones render as empty rather than
    # breaking, which is how the existing profile behaves too.
    out.setdefault("months", 4)
    # Marks this as somebody else's message. compose.py uses it to stop saying
    # "a pipeline I built" in the name of a person who did not build it.
    out["_borrowed"] = True
    return out


async def run_stream(posting: str, scenario: str | None, client_ip: str,
                     sender: dict | None = None):
    """
    Drive one run, yielding events as they happen.

    The agent is synchronous and talks to a database and an API, so it runs in a
    worker thread and pushes events onto a queue this drains. Nothing in here may
    raise into the response: a half-written stream is the one outcome the page
    cannot render, so every path ends with a terminal event.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    conn = None

    def emit(payload: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    def work() -> None:
        try:
            with conn.cursor() as cur:
                # Clear expired visitor rows first. A demo that accumulates state
                # stops demonstrating anything: every company anyone tried would
                # read as already contacted, and the happy path would disappear.
                sandbox.sweep(cur)

                allowed, reason = limits.check_and_reserve(cur, client_ip)
                if not allowed:
                    emit({"kind": "limited", "note": reason})
                    return

                found = resolver.resolve(cur, posting)
                if not found["found"]:
                    limits.refund(cur, client_ip)
                    emit({"kind": "unavailable", "note": found["reason"],
                          "suggestions": found.get("suggestions", [])})
                    return

                job = resolver.posting_for(cur, found["company"])
                if not job:
                    limits.refund(cur, client_ip)
                    emit({"kind": "unavailable",
                          "note": f"{found['company']} has no open role to run against.",
                          "suggestions": found.get("suggestions", [])})
                    return

                emit({"kind": "resolved", "company": job["company"],
                      "title": job["title"], "fit_score": job["fit_score"],
                      "note": (f"Matched to {job['company']}. Running against a real "
                               f"open role there: {job['title']}.")})

                import anthropic
                # .strip(), and it is not defensive tidiness. A key pasted into a
                # hosting dashboard arrives with whatever came with it, and a
                # single trailing newline makes an invalid HTTP header, which the
                # SDK reports as APIConnectionError: Connection error - a message
                # that says the network is down when the network is fine. This
                # service ran for two hours telling every visitor "that run could
                # not be completed" for exactly that reason, with a valid key in
                # the variable. One character, and the error names the wrong
                # subsystem, so the cost is measured in hours of looking at the
                # wrong thing.
                api = anthropic.Anthropic(
                    api_key=os.environ["ANTHROPIC_API_KEY"].strip(),
                    timeout=30.0, max_retries=1)
                context = DraftingContext(cur, [job["company"]], sender)
                record = agent_mod.process_job(
                    cur, job, context, api, MODEL, DRAFT_MODEL,
                    agent_mod.MAX_STEPS_PER_JOB, on_event=emit)

                # Only when THIS run drafted. The row keeps whatever was written
                # last time, so reading it unconditionally showed an old draft after
                # a run that had correctly refused to write a new one - the console
                # displayed a message beside the words "skipping to avoid a
                # duplicate", which is precisely the wrong impression.
                if record.get("drafted"):
                    cur.execute("SELECT draft_email FROM outreach_tracker "
                                "WHERE source || ':' || job_id = %s", (job["job_id"],))
                    row = cur.fetchone()
                    if row and row[0]:
                        emit({"kind": "draft", "text": row[0]})
                emit({"kind": "done", "note": outcome_note(record),
                      "outcome": record.get("outcome")})
        except Exception as exc:                                     # noqa: BLE001
            log.exception("run failed")
            emit({"kind": "error",
                  "note": "That run could not be completed.",
                  "detail": f"{type(exc).__name__}"})
        finally:
            emit({"kind": "_end"})

    try:
        conn = demo_connection()
    except Exception:                                                # noqa: BLE001
        log.exception("no database")
        yield sse({"kind": "error", "note": "The service cannot reach its database."})
        yield sse({"kind": "done", "note": "Nothing was run."})
        return

    task = loop.run_in_executor(None, work)
    deadline = time.monotonic() + RUN_TIMEOUT_S
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield sse({"kind": "error", "note": "That run took too long and was "
                                                    "stopped."})
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                yield sse({"kind": "error", "note": "That run took too long and was "
                                                    "stopped."})
                return
            kind = event.get("kind")
            if kind == "_end":
                return
            # The loop emits an "outcome" event carrying the whole record, which is
            # for callers that want it rather than for display - the "done" event
            # below says the same thing in a sentence. Passing it through streamed a
            # blank line to the console.
            if kind == "outcome":
                continue
            # A step record becomes a display event here; anything already shaped
            # for display passes through untouched.
            yield sse(describe(event) if "tool" in event else event)
    finally:
        task.cancel() if hasattr(task, "cancel") else None
        if conn is not None:
            conn.close()


@app.post("/api/agent/run")
async def run(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:                                                # noqa: BLE001
        pass
    posting = (body.get("posting") or "").strip()[:8000]
    scenario = body.get("scenario")
    sender = clean_sender(body.get("sender"))

    # The visitor's IP, behind Railway's proxy. Best effort: this is a courtesy
    # limit, and the spend ceiling is the control that actually matters.
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = (forwarded.split(",")[0].strip()
                 or (request.client.host if request.client else "unknown"))

    return StreamingResponse(
        run_stream(posting, scenario, client_ip, sender),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


@app.get("/api/agent/stats")
async def stats():
    try:
        conn = demo_connection()
    except Exception:                                                # noqa: BLE001
        return JSONResponse({"available": False})
    try:
        with conn.cursor() as cur:
            return JSONResponse({"available": True, **limits.totals(cur)})
    finally:
        conn.close()


@app.get("/healthz")
async def healthz():
    """Liveness only. A database check here would let a Neon blip restart a
    perfectly healthy process, which is the opposite of what this is for."""
    return {"ok": True, "at": datetime.now(UTC).isoformat()}
