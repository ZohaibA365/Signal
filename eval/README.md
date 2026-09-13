# Enrichment eval

Measures how well the LLM layer agrees with you, so a prompt edit or a model
switch cannot quietly make the board worse.

The enrichment layer makes three judgements about every posting and all three
reach the published site. This scores them against a hand-labelled set and writes
a result you can diff against a previous run.

```bash
python eval/run_eval.py --dry-run     # no API call, no cost - use this to test changes
python eval/run_eval.py               # a real run on Haiku, ~$0.02 for five examples
python eval/run_eval.py --compare-to eval/results/eval_20260913T210717.json
```

## What is scored

| field | values | how it is scored |
|---|---|---|
| `sponsorship_signal` | `sponsors`, `no_sponsorship`, `unclear` | exact match |
| `eligibility` | `eligible`, `blocked`, `unclear` | exact match |
| `fit_score` | 0-100 | pass if within **10** points; mean absolute error also reported |

These are the real field names from `ai_layer/enrich.py`. There is no
`visa_sponsorship` and no `opportunity_score`.

**Sponsorship and eligibility are different questions, deliberately.**
`sponsorship_signal` reports only what the posting text says. `eligibility` is the
overall call and is allowed to use what is well known about the employer. So a
defence contractor whose text never mentions visas is correctly
`sponsorship_signal: unclear` and `eligibility: blocked` at the same time. Do not
label them as if they must agree.

### Why the fit tolerance is 10 and not zero

`fit_score` is a judgement, not a measurement. Nothing pins the model's
temperature, so two runs of an unchanged prompt differ by a few points on their
own, and the prompt itself only asks for bands - "most postings are mediocre and
should score in the 20-50 range", "reserve above 80 for genuinely strong".

Ten points is the width that asks the question worth asking: *did the judgement
move bands?* Noise passes. A posting moving 45 to 75 - `worth a look` to
`apply now` on the board - fails. Exact match would fail on nothing but noise;
a tolerance of 25 would not notice that move at all. The constant lives at the top
of `eval_utils.py` with this reasoning beside it.

## Adding an example

Append an object to `golden_set.json`:

```json
{
  "job_id": "company_board:lever-abc123",
  "source": "company_board",
  "company": "Acme Data",
  "title": "Data Engineering Co-op, Winter 2027",
  "country": "ca",
  "location": "Toronto, Ontario",
  "state": "Ontario",
  "posted_date": "2026-09-08",
  "seniority": "intern",
  "salary_min": null,
  "salary_is_predicted": null,
  "raw_posting_text": "the posting's actual description",
  "expected_sponsorship_signal": "unclear",
  "expected_eligibility": "eligible",
  "expected_fit_score": 82,
  "notes": "why you labelled it this way"
}
```

The posting text is stored **inline, not as a database pointer**, on purpose:
`storage/prune.py` drops description text a week after a posting arrives once S3
holds it, so a pointer would rot and the eval would lose its inputs. The whole set
is self-contained, which is also why it needs no database and no warehouse
credentials.

A malformed entry fails before any API call is made, with a list of what is
wrong - the harness will not spend money on a broken set.

**The five examples shipped here are placeholders.** The text is real, pulled from
the warehouse, but the labels are mine, not yours. Replace them. Five examples
measure very little: accuracy moves 20 points per example, so treat this as the
mechanism and not yet as evidence. It starts meaning something around 30-50.

## Reading a result

Every run writes `eval/results/eval_<timestamp>.json` containing per-example
expected against actual, the model's own reasoning for each judgement, the model,
the profile, the git commit and a hash of the system prompt.

**Look at the reasoning first.** It is stored for exactly this purpose. The first
real run of this harness found two things in about a minute:

- The prompt defines `eligibility` as a *legal* question - citizenship, clearance,
  work authorisation - but the model returned `blocked` for a Principal-level role
  because "the candidate is a student... would not qualify". That is a
  qualification judgement wearing eligibility's clothes, and it matters because
  `apply_queue` turns `blocked` into `skip`.
- It marked a Lyft internship `eligible` on the grounds that "Lyft routinely
  sponsors J-1 visas for student interns", which the prompt explicitly permits.
  The label in this repo says `unclear`. That one is probably the label being
  wrong rather than the model.

Both are the harness doing its job. Neither has been changed in the prompt.

## The two caveats that matter

**Model.** Runs default to Haiku because runs cost money. The daily pipeline
scores intern and entry roles - the ones actually applied to - with **Opus**, and
only the rest of the board with Haiku. A green Haiku eval therefore says nothing
about the path the important roles take. Every run prints this. Use
`--model claude-opus-5` to test that path.

**Profile.** The system prompt is built from the candidate profile, so a different
profile is a different prompt and the numbers are not comparable across them.
`ai_layer/candidate_profile.py` defaults to `cobol`; the daily pipeline sets
`SIGNAL_PROFILE=student`. The harness pins `student` to match, and records the
profile in every result. The first run of this harness did not, scored every
posting against a mainframe programmer, and explained itself with "no mainframe or
COBOL involvement" - which is how the gap was found.

## Comparing two runs

`--compare-to` pairs the runs on `job_id` and classifies every field as
`regression` (passed before, fails now), `improvement`, `unchanged`, `new` or
`dropped`. Only regressions are marked in red, and the run exits non-zero when
there are any, so this can become a CI gate later.

A regression is narrower than "the answer changed". A `fit_score` moving 40 to 44
against an expected 42 changed and is not a regression; reporting it as one would
teach you to ignore the output.

If both runs share a `prompt_sha256`, the harness says so: any difference between
them is the model answering differently, not an edit you made.

## Flags

| flag | what it does |
|---|---|
| `--dry-run` | Scores expected values against themselves. No API call, no cost. Always 100%, which is the point: anything else means the harness is broken. |
| `--model` | Default Haiku. `claude-opus-5` for the path your key roles take. |
| `--profile` | Default `student`, matching the pipeline. |
| `--limit N` | Score only the first N examples. |
| `--compare-to PATH` | Diff against a previous results file. |
| `--fail-under PCT` | Exit non-zero if any rate falls below PCT. Off by default. |
| `--out PATH` | Write results somewhere other than `eval/results/`. |

## Design notes

`eval_utils.py` is pure functions with no I/O and no API calls, so the scoring is
testable without a key or a network (`tests/test_eval_utils.py`) and so a Great
Expectations custom expectation or an Airflow task can call `summarise()` later
without dragging the console code along. Note that `quality/expectations.py` is
hand-rolled and Great Expectations is **not** actually installed here, so
"wrap it in GE" means adding that dependency first.

`run_eval.py` imports and calls `ai_layer.enrich.assess()`. It does not
reimplement the prompt, the schema or the API call - a harness scoring its own
copy of the prompt would keep passing while the real one broke.

No new dependencies. The table is f-strings, matching `quality/expectations.py`.
