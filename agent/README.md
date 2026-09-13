# Outreach agent

Walks high-scoring postings, drafts outreach for each, records the result. Uses
Claude's tool-use API, with three tools and no way to send anything.

```bash
python agent/agent.py --dry-run --limit 3 --no-write   # no API call, no writes
python agent/agent.py --limit 1                        # one posting, for real
python agent/agent.py --job-id company_board:lever:abc123
pytest agent/tests -q                                  # the safety rails
```

## What it does

For each posting, one at a time: check the tracker, draft if allowed, record it.
The expected path is three tool calls. The model chooses; the code decides whether
each choice is permitted.

It can only ever move a posting from `not_contacted` to `email_drafted`. There is
no send tool and no way to mark anything sent.

## The safety rails

Each one is enforced in code. None depends on the model following instructions,
because the posture throughout is that the model may be wrong or adversarial.

**Duplicate prevention.** `check_application_status` returns `eligible_to_draft`,
computed in code, false for anything at `email_drafted` or beyond. The draft tool
checks the same thing again independently, so a model that skips the status check
gets the same refusal. Enforcing it in only one place would make it advice.

**Never trust a model value that should have come from the database.** The draft
tool takes a `company` argument and compares it against the company stored for that
`job_id`. A mismatch is rejected and logged, not quietly corrected. A posting the
model invented fails earlier: it is not in `raw_postings`, so no tracker row is
created for it.

**Transitions the agent may not make.** `email_sent` and `responded` describe
things a person did in the world. An agent able to write them could report an email
it never sent, and the tracker would become a record of fiction. Two rails refuse
it: the tool schema offers only `email_drafted`, and `validate_transition` refuses
underneath that if the schema were ever widened.

**A status must be earned.** Recording `email_drafted` is refused unless a draft
actually exists. The first dry run marked a posting drafted after its draft call
had been rejected, which is how this rail got written.

**Step cap: 8 tool calls per job.** Per job, not per run — the happy path is three,
so a cap across a 30-job batch would die on the second posting. Eight leaves room
for a retry and a re-check while still stopping a loop that has gone wrong. Hitting
it is recorded as the outcome `step limit reached`, not as an error, because it is
information about the model.

**No silent failures, and no fatal ones.** A tool never raises into the loop. Bad
arguments, a missing posting, a database error and a model failure all become a
recorded result the model can read and the run continues to the next posting. One
bad posting must not take a batch with it.

## Grounded drafting: the model writes, code proves

`outreach/compose.py` assembles messages deterministically from insight records
that came out of SQL, so by construction "a message cannot contain a figure the
warehouse cannot reproduce". Letting a model write the prose buys better sentences
and would throw that away — unless something checks.

So the model drafts from the insight records, and then `agent/verify.py` proves it:

1. **Every number must trace to an insight.** Harvested from the evidence dicts and
   from the insight text, because derived figures like `3.1x` and `17%` exist only
   in the text. Correct roundings are allowed: an insight reading "about 12.1x"
   permits "12x", because the reader is not misled. `13x` is refused.
2. **The link must be exact.** A plausible invented URL is the most damaging
   hallucination available, because it is the one thing the reader is invited to
   click.
3. **The company must be named.**
4. **Channel budgets**, reusing `compose.CONNECTION_LIMIT` for LinkedIn's 300
   characters.
5. **Playbook prohibitions**: no requisition number, and no claim of having worked
   at, applied to or spoken with the company.

A draft that fails is discarded, the deterministic template is used instead, and
**the rejected text is kept in the log** — "the model fabricated something" is not
actionable, but the sentence it fabricated is. So the worst case is the old
behaviour, and the log tells you how often the model invents things.

All three variants pass or none is used. Mixing a verified model email with a
template follow-up would make "who wrote this" unanswerable per message, and that
is the first question worth asking about anything you are about to send.

### What this does not prove

It proves every number in the text appears in the source records. It cannot prove
the sentence around the number is a fair reading of it — "hiring has collapsed to
12 roles" and "hiring is up to 12 roles" both verify. Numeric grounding is
necessary, not sufficient.

## Reading a log

One JSON file per run in `agent/logs/`, named `run_<UTC timestamp>.json`. Every
tool call is recorded with its arguments, result, duration, timestamp and outcome,
**including rejected and failed calls**, which are the interesting ones. The run
summary counts postings, companies, drafts by the model against drafts by the
template, and rejected calls.

The draft text itself lives on the tracker row rather than in every log entry,
which would triple the file. The exception is a rejected draft, which is kept in
the verification report because there is nowhere else to read it.

## Models

Haiku for both orchestration and drafting. Orchestration is choosing among three
tools and passing an id through, which needs nothing more. Drafting is the one
place a stronger model reads better: `--draft-model claude-opus-5` switches it, for
roughly five times the cost. It is not a safety tradeoff — the verifier applies
identically to both.

## The tracker

`outreach_tracker` in Postgres, keyed on `(source, job_id)` like every other
per-posting table. Declared in `storage/schema.sql`, so the daily pipeline's schema
step deploys it.

`draft_source` records `model` or `template`, because who wrote a draft is the
first thing worth knowing about one you are about to put your name on.

## Honest note

This does not need an LLM. Check, draft, update is a `for` loop, and it would be
cheaper and fully deterministic. What the tool-use layer buys is a model that can
decide to skip, and a demonstration of the pattern with the rails built properly.
What the drafting step buys is genuinely better prose without giving up the
guarantee — and that part does need a model.

## Not built, on purpose

No `send_email`. `agent/tools.py` marks where it would attach and the shape it
should take: it must refuse unless the row is already at `email_drafted` and
carries an explicit human approval marker, and it must never be handed to the model
as a fourth tool. Sending is the one action here that cannot be undone.
