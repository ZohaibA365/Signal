# Signal

**A job board for US and Canadian data roles, built on a warehouse that explains them.**

🔗 **[signal-jobsite.vercel.app](https://signal-jobsite.vercel.app)**

Signal collects data and AI job postings from employers' own career boards, models
them through a warehouse, and publishes two things. There is a searchable job board
where every link goes straight to the employer. There is also a market index
tracking what the market is actually hiring for.

It is aimed at people who need to know which employers actually sponsor work
authorisation, which is the question job boards are worst at answering.

---

## What it does

1. Resolves each employer to its applicant-tracking system, cached so a name is
   answered once. Ten adapters cover Greenhouse, Lever, Ashby, Workday,
   SmartRecruiters, Workable, Rippling, Oracle Cloud, Amazon's own API and a shared
   base. They have resolved 1,627 boards out of 8,316 companies tried.
2. Ingests postings from those boards into an S3 data lake, Hive-partitioned by
   source, country and date.
3. Loads them into Postgres with idempotent upserts, so re-runs and backfills are
   safe.
4. Transforms through dbt. Staging, then intermediate, then marts, plus a star
   schema, deduplicating to one row per distinct role.
5. Processes 595,431 Department of Labor visa filings with PySpark, joining them to
   employers so sponsorship is a matter of record rather than inference.
6. Extracts technology mentions with a controlled vocabulary of 120 tools.
7. Snapshots market-wide demand daily. That accumulates a time series that exists
   nowhere else.
8. Assesses roles with Claude. It returns work-authorisation eligibility,
   sponsorship signals, fit against a profile, and the reasoning behind each call.
9. Drafts cold outreach with a tool-using agent. It looks the company up in the
   warehouse, finds what is genuinely unusual about their stack, and writes an email
   about it. The rails live in code rather than in a prompt, so any sentence the
   query results do not support gets rejected. It refuses instead of inventing.
10. Publishes a static site of 1,248 pages with client-side search and no backend.

## Architecture

```
career boards ─┐
(Greenhouse,   │
 Lever, Ashby, ├─► S3 data lake ──► Postgres ──► dbt ──► Next.js export
 Workday,      │   (raw JSON,       (idempotent  (staging → ├─ job search
 SmartRecruit, │    Hive-           upserts)     marts +    ├─ company pages
 Workable,     │    partitioned)                 star       ├─ market index
 Amazon)       │                                 schema)    └─ outreach agent
Adzuna ────────┘                                     ▲
                                                     │
DOL visa filings ──► PySpark ────────────────────────┤
taxonomy.py (120 technologies) ──────────────────────┤
Claude API (eligibility, fit, reasoning) ────────────┘
```

| Layer | Technology |
|---|---|
| Ingestion | Python, 10 ATS adapters, boto3 |
| Data lake | AWS S3, Hive-style partitioning |
| Warehouse | PostgreSQL (Docker local, Neon hosted), mirrored to Snowflake and Databricks for parity |
| Transformation | dbt, 15 models, 113 nodes including 97 tests |
| Batch processing | PySpark over 595k visa filings |
| Streaming | Kafka (KRaft), producer + alerting consumer |
| Orchestration | Airflow |
| Enrichment | Claude API (Opus 5), structured outputs |
| Outreach agent | Claude tool use over the warehouse, FastAPI + SSE |
| Frontend | React 19, TypeScript (strict), Next.js static export, Tailwind |
| Internal tool | Ruby 3.4 / Rails 8.1, Hotwire, RSpec, reads the warehouse and owns one schema |
| Delivery | 1,248 prerendered pages on Vercel, no server |
| Testing | pytest (544 tests), 108 RSpec examples, 97 dbt tests, CI with a Postgres service |

## Scale

- 71,404 postings from 4,048 companies, 58,599 of them from employers' own boards
- 1,627 career boards resolved, out of 8,316 companies tried and cached either way
- 224,737 technology mentions across 120 tracked tools
- 595,431 DOL filings over 5,751 employers, 1,596 of which matched a company here
- 1,248 published pages, made up of 1,123 companies, 120 technologies and 5 top level
- 20,000 roles searchable in the browser over a 735 kB gzipped payload, no backend

---

## Why career boards, not an aggregator

The job board originally ran on aggregator data and 90% of it was unclickable.

An aggregator redirect is gated by country. Opened from Canada, a US posting shows
"not available in your region". The API never exposes the employer's own URL, and
the redirect returns 403 to anything that is not a browser, so I could not resolve
it either. Those links were not broken. They were unrepairable.

Employers' own boards give the real URL by construction. The cost is that board
coordinates cannot be guessed. Slug-guessing against the startup boards resolved 5
of the 60 largest employers. The rest are on Workday, whose tenant *and* site path
are arbitrary strings. Capital One is `capitalone.wd12` with site `Capital_One`, not
`wd1` and not `Careers`. SmartRecruiters is the same, with `BoschGroup` rather than
`bosch`. So I read coordinates off a real careers URL once and cache them in a
registry.

The switch paid twice. Board descriptions run 1,200 to 8,400 characters against the
aggregator's 500-character truncation. The visa language that decides sponsorship
sits at the *end* of a posting, which is why those verdicts had mostly been
"unclear".

---

## What the data shows

The warehouse market is a two-horse race. Databricks (36%) and Snowflake (34%)
together hold 70% of warehouse demand. Redshift is AWS's own product and sits at
7.5%, behind BigQuery.

Newer tooling pays best. Share of postings in the top salary band runs DuckDB 96%,
Flink 92%, ClickHouse 90%, Iceberg 89% and Dagster 84%, against a mid-70s field for
established tools.

Stacks cluster hard, and the tightest cluster is the oldest one. 88% of postings
mentioning JCL also mention COBOL. That is 270× what their volumes alone would
predict, and CICS, Db2 and VSAM sit in the same knot. The co-occurrence model
rediscovered the mainframe with no prior knowledge that it exists. At the other end
Dagster and Prefect co-occur at 305×. They are direct competitors, named together
because the teams hiring for one are evaluating both.

---

## How AI was used

Claude reads each posting and returns a structured assessment. It covers whether a
sponsored international student could hold the role, what the text says about
sponsorship, a fit score, and one-sentence reasoning for each. I separated
eligibility from sponsorship signal on purpose. The latter reports only what the
posting states, while the former may draw on well-established knowledge.

I also took the LLM out of one place. Technology extraction originally used it and
produced unusable output, with "Analytics" and "analytics" as separate entries and
"AI", "AI/ML" and "AI/LLM" as three things. A dictionary matcher replaced it, which
is cheaper, deterministic and reproducible. Published numbers have to be
re-derivable by anyone, and free-text model output is not. Peer companies work the
same way. They come from Jaccard similarity over technology sets with no model call,
and they are suppressed entirely below a confidence floor.

The outreach agent gives Claude three tools over the warehouse and lets it decide
what to look up. A verifier then checks everything it may claim, and that verifier
never asks the model anything. It rejects and redrafts a draft that names a company
the query did not return, quotes a figure no row contains, or reaches for a ratio
the insight layer deliberately withheld. Prompts are advice. The rail is the only
thing that holds. The console on the site shows the rails firing on purpose. Ask it
about a company that is not in the data and watch it refuse rather than produce
something plausible.

---

## The internal tool

`tracker/` is a Rails 8 app for working the pipeline's output. It browses roles
with their eligibility and fit scores, tracks each application from saved through
applied, interviewing and then rejected or offer, and approves the agent's drafts
before any of them can be marked as sent. It sits behind a login and is not part of
the public site. There is a read-only sample account on the sign-in page if you
want to see it.

The interesting constraint is not the app. It is the boundary. **Rails can read the
warehouse and is structurally incapable of writing to it**, enforced three times
over:

- `database_tasks: false` on the warehouse connection takes it out of migrations,
  schema management and seeds entirely, so a migration cannot target a dbt relation
  even by mistake.
- `replica: true` makes ActiveRecord refuse writes on that connection.
- A `signal_rails` Postgres role holds `SELECT` on two named tables and no
  `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` anywhere in `public`.

`tracker/script/verify_isolation.rb` proves all three the way `service/sandbox.py`
does. It attempts the forbidden writes and fails loudly if any succeeds, and it
insists each refusal comes for the *right* reason. That caught a check that was
passing because a table was missing rather than because the write was refused.

It also found a real defect in the design. Through Neon's connection pooler the
app's two connections reported the same `pg_backend_pid()`. PgBouncer in transaction
mode had multiplexed them onto one server backend, where `search_path` is session
state, so the two connections silently swapped schemas. The whole read-only split
depends on those paths differing. Both the app and its migrations now use the direct
endpoint, and the proof script asserts the backend pids differ.

Approval is a marker rather than a status. `agent/tools.py` already specified the
shape. A send must refuse unless the row is at `email_drafted` *and* carries an
explicit human approval. A new status value would have broken three things,
including that `ALREADY_DRAFTED` is derived by slicing `STATUSES`. So approval lives
in a Rails-owned table, pinned to a sha256 of the exact text approved. If the agent
redrafts afterwards the approval is recognisably stale rather than silently
authorising words nobody read. `agent/show_drafts.py --mark-sent` refuses without it
and names which of four things is wrong.

Running the tracker locally:

```bash
cd tracker && bin/dev                       # http://localhost:3000
bin/rails runner script/verify_isolation.rb # prove the read-only split
bin/rails tracker:backfill DRY_RUN=1        # what it would save from the queue
bundle exec rspec                           # 108 examples
```

---

## Running it locally

```bash
git clone https://github.com/ZohaibA365/Signal.git && cd Signal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env        # then fill in your API keys
docker compose up -d        # Postgres with the schema applied

python ingestion/board_discovery.py --top 100    # resolve boards, cached
python ingestion/board_ingest.py                 # postings → S3
python storage/load_to_warehouse.py --boards     # S3 → Postgres
python ai_layer/extract_tech.py                  # technology mentions
python ingestion/market_snapshot.py              # daily market capture

cd dbt_signal && dbt build --profiles-dir .      # build all models
cd .. && python site/export_data.py              # warehouse → JSON for the frontend

cd web && npm install && npm run build           # 1,248 prerendered pages → web/out
```

The frontend is a Next.js static export running React 19 and TypeScript under
`strict`, with no server. Python owns the warehouse and hands it JSON. Next knows
nothing about Postgres.

The one thing the build does not embed is the 4 MB search payload. The daily
pipeline rebuilds it, and the site fetches it at runtime from wherever that pipeline
publishes it. So the repository carries no four-megabyte file that changes every
night, and a build from last week still shows today's roles.

Run the tests with `pytest`. To run what CI runs, against a throwaway Postgres
container and an environment built from `requirements-dev.txt` alone:

```bash
CLEAN=1 bash scripts/ci_rehearse.sh
```

I compare three engines weekly, and on demand, because two of them fail in ways the
third cannot see. Snowflake catches regex anchoring, Databricks catches type
coercion, and Postgres serves the site.

```bash
python storage/load_to_snowflake.py && dbt build --target snowflake
python storage/load_to_databricks.py && python storage/build_on_databricks.py
python storage/parity_check.py        # 21 comparisons, all three engines
```

Four of the twenty-one compare source row counts before anything else runs. A stale
mirror otherwise fails as "the engines disagree", which reads exactly like a dialect
bug and is nothing of the kind.

Employer matches the machine will not guess at are queued for a person:

```bash
python storage/review_matches.py --status        # what is undecided, and its cost
python storage/review_matches.py --emit          # a worksheet with the evidence
python storage/review_matches.py --apply review_queue.csv
python storage/load_dol.py --rebuild-mapping-only
```

Enrichment (`python ai_layer/enrich.py --seniority intern entry --linkable --relevant`)
needs an Anthropic key with credit. It is incremental, so a posting is re-scored only
when its description changes. The `--linkable` flag restricts it to postings the site
can actually publish, since scoring one it cannot show buys nothing.

## Repository layout

```
ingestion/      ATS adapters, board discovery + ingest, aggregator, market snapshot
storage/        S3 → warehouse loader, schema, connection helper, Parquet export,
                mirrors to Snowflake and Databricks, the engine parity check,
                retention and the employer review queue
processing/     PySpark aggregation over DOL visa filings
ai_layer/       taxonomy, extraction, LLM enrichment, candidate profiles
dbt_signal/     staging → intermediate → marts, star schema, macros, tests
site/           warehouse → JSON export for the frontend, legacy Jinja generator
web/            Next.js frontend — React 19, TypeScript, Tailwind, static export
agent/          the outreach agent: tool loop, verifier rails, recorded runs
service/        FastAPI streaming endpoint behind the live console
tracker/        Rails 8 app: browse roles, track applications, approve drafts
outreach/       per-company insights and message drafting
streaming/      Kafka producer and alerting consumer
orchestration/  Airflow DAG
quality/        expectation checks run in the pipeline
analytics/      history rollups for the trend pages
dashboard/      Streamlit views over the same marts, for exploration
eval/           golden-set evaluation of the enrichment model
scripts/        CI rehearsal, DOL download, credential helpers
tests/          pytest suite
```

## Data honesty

Two demand series exist and they are not equally trustworthy:

- `market_demand` comes from the market-wide index. Trustworthy.
- `tech_demand_history` is reconstructed from postings actually collected. It is a
  biased sample, because roles that filled quickly have disappeared. I show it only
  for months with a usable sample and never present it as market-wide demand.

Salary figures come from the market-wide histogram, never from per-posting salary
fields. Roughly 99% of those are model estimates rather than employer-published
figures.

The job list shows only postings whose link was verified to reach the employer.
Everything else still counts toward the market index, where no link is involved.

Peer comparisons state what their baseline is. It is every tracked company above a
posting floor, not a curated peer set. The pages say "the companies I track" rather
than "comparable companies", and a company is excluded from the baseline it is
measured against.

---

Built by [Zohaib](https://github.com/ZohaibA365)
