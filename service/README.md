# The public agent service

Runs one agent run for a website visitor and streams it to their browser. Same
agent code as the command line; different entry point, different database role.

```bash
uvicorn service.app:app --reload --port 8000     # local
```

## Why the page cannot break when this does

The page gives this two seconds to answer and plays a recorded run on anything
else: a timeout, a 502 mid-deploy, the daily budget ceiling, a dropped stream
halfway through. Every failure looks the same to a visitor, and none of them looks
like a failure.

That is why this file can afford to refuse work freely. Refusing is invisible.

## What confines public traffic

Two mechanisms, and neither is a convention a future edit could step around.

**A separate schema.** The agent's SQL says `outreach_tracker` unqualified, so a
connection that sets `search_path TO demo, public` resolves it to the sandbox copy
while `raw_postings`, `apply_queue` and `dim_company` still resolve from `public` -
because `demo` does not contain them. The agent's SQL is unchanged.

**A role that cannot reach production.** `signal_demo` has SELECT on ten warehouse
tables, named individually so a future table holding something private does not
become readable by accident, and INSERT/UPDATE on exactly two demo tables. It has
no DELETE anywhere, which is not an oversight - the sweep below was written as a
DELETE first and the database refused it.

`python service/sandbox.py --verify` proves both, by attempting a write to
production and expecting to be refused.

## What stops it becoming an open wallet

Per-visitor limits are polite; the daily spend ceiling is the control. Anyone who
wants to get past an IP limit rotates addresses, so the ceiling is kept in the
database rather than in the process: a counter that resets on redeploy is not a
budget, and a redeploy is exactly when someone would be hammering it.

Budget is reserved *before* the model is called and refunded if the run does not
happen, so simultaneous requests cannot all pass a check that each of them then
invalidates.

| Variable | Default | What it does |
|---|---|---|
| `AGENT_RUNS_PER_IP` | 5 | Per visitor, per day |
| `AGENT_DAILY_BUDGET_USD` | 2.00 | Hard ceiling, about 100 runs |
| `AGENT_ESTIMATED_RUN_USD` | 0.02 | Reserved per run before spending |
| `AGENT_RUN_TIMEOUT_S` | 45 | A run that has not finished by now is stopped |

## The sandbox resets itself

Every visitor run leaves a row at `email_drafted`. Without a sweep, every company
anyone tried would read as "already contacted" within a week and the happy path
would disappear - the demo would degrade into only ever showing the duplicate rail.
Rows older than two hours are reset to `not_contacted`, except the seeded ones,
which the duplicate rail needs to stay permanent.

## Deploying

Railway, from `service/Dockerfile`. Environment variables:

```
DEMO_DATABASE_URL      postgres://signal_demo:...@...  (NOT the owner credentials)
ANTHROPIC_API_KEY      ...
AGENT_ALLOWED_ORIGINS  https://signal-jobsite.vercel.app
```

`AGENT_ALLOWED_ORIGINS` must name the origin the site is actually served from. It
is the whole of the access control here, so a stale value does not degrade the page
— it fails the preflight and the console silently falls back to recordings.

Then point the frontend at the service URL and the page starts using it:
`NEXT_PUBLIC_AGENT_API` for the Next build in `web/` (set in `web/vercel.json`),
`SIGNAL_AGENT_API` for the older Jinja build. Unset, the page plays recordings and
is complete.

## Endpoints

- `POST /api/agent/run` — one run, streamed as server-sent events.
- `GET /api/agent/stats` — counters. Returns `available: false` rather than an
  error when the database is unreachable.
- `GET /healthz` — liveness only. A database check here would let a Neon blip
  restart a healthy process, which is the opposite of the point.
