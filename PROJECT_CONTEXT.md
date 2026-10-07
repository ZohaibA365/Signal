# Signal — Full Project Context

> A single-file briefing on what Signal is, how it is built, why each decision was
> made, and what is still broken. Written to be handed to an AI assistant (or a new
> contributor) so it understands the whole system without reading 30,000 lines.
>
> **Live:** https://signal-jobsite.vercel.app · **Repo:** https://github.com/ZohaibA365/Signal
> **Numbers verified:** 2026-10-07, against the production warehouse.
> **Contains no credentials.** Every secret is referenced by variable name only.

---

## 1. What Signal is

A job board for US and Canadian data/AI roles, built on a data warehouse that
explains the market those roles sit in.

Two products share one pipeline:

1. **A job board** where every link goes directly to the employer's own careers
   page — no aggregator redirects.
2. **A market index** tracking which technologies are actually being hired for,
   accumulated daily, with company-level and technology-level pages.

Plus a third thing that is really a demonstration of the first two: **an outreach
agent** that drafts a cold email about a company using only facts the warehouse can
reproduce, and refuses when it cannot.

### Why it exists

It began as one personal problem — a Canadian student looking for a US internship
that would sponsor a visa — and the discovery that **no job board can answer "will
this employer sponsor me?"** Job text almost never says: under 4% of postings
mention sponsorship at all, even at full length. So the project went and got the
Department of Labor's actual record of who filed to sponsor, and joined it to
employers. That turns an inference into a fact.

The market index was the second discovery: the market turned out to be more
interesting than any single posting.

### Who built it and why that shapes the design

Built solo by Zohaib (Management Engineering, University of Waterloo, graduating
Spring 2028), targeting a **Winter 2027 US data-engineering co-op requiring visa
sponsorship**. Two consequences show up all over the codebase:

- **Everything runs on free tiers.** Neon, Vercel, Railway, GitHub Actions,
  Databricks Free Edition, Snowflake trial (expired). Several architectural choices
  are workarounds for free-tier limits, and they are documented as such rather than
  dressed up.
- **Public numbers must be defensible.** The site makes claims a stranger — possibly
  a recruiter at the company being described — can check. That single constraint
  explains most of the unusual engineering below: the LLM was *removed* from
  entity extraction, peers are suppressed below a confidence floor, trend claims are
  gated on collection history, and a verifier rejects model prose that cannot be
  traced to a query.

---

## 2. Current scale (verified 2026-10-07)

| Measure | Value | How to re-derive |
|---|---|---|
| Raw postings collected | **98,421** | `select count(*) from raw_postings` |
| Postings after dedup (fact grain) | **87,048** | `select count(*) from fct_job_posting` |
| — from employers' own boards | **68,674** | `… where source='company_board'` |
| — from the aggregator | **18,374** | `… where source='adzuna'` |
| Companies | **4,928** | `select count(*) from dim_company` |
| Career boards resolved | **1,627** of 8,316 tried | `board_registry where status='resolved'` |
| ATS platforms with resolved boards | **7** | `count(distinct ats) … status='resolved'` |
| Technology mentions | **273,826** | `select count(*) from posting_technologies` |
| Technologies tracked | **120** | `select count(*) from dim_technology` |
| DOL visa filings processed | **595,431** | `select sum(filings) from dol_employer_summary` |
| DOL employers | **5,751** (15,400 employer-year rows) | `count(distinct employer_key)` |
| Companies matched to a DOL filer | **1,685** | `select count(*) from company_employer_link` |
| LLM-enriched postings | **4,280** | `select count(*) from job_enrichment` |
| Data-derived peer pairs | **2,550** | `select count(*) from int_company_peers` |
| Daily market snapshots held | **38 days** (since 2026-08-27) | `count(distinct snapshot_date) from market_demand` |
| Technology co-occurrence pairs | **7,034** | `select count(*) from tech_cooccurrence` |
| Tables in the warehouse | **35** | `information_schema.tables where table_schema='public'` |
| Published pages | **1,248** (1,123 companies + 120 tech + 5) | `grep -c '<loc>' sitemap.xml` |
| Searchable payload | 20,000 rows, 4.0 MB raw / ~735 kB gzipped | `web/public/data/jobs.json` |
| dbt | **15 models, 113 nodes, 97 tests, 10 sources** | `dbt_signal/target/manifest.json` |
| Engine parity | **21 comparisons** (4 source counts + 17 checks) | `storage/parity_check.py` |
| Python test suite | **529 tests** | `pytest` |
| Code | ~30,500 lines across 254 tracked files | 20.5k Python, 3.5k TS/TSX, 2.2k SQL |

**Numbers in the README are the ones to trust** — they are re-derived from the
warehouse, not carried forward. Anything quoted from memory is probably stale: the
corpus roughly doubles every six weeks right now.

---

## 3. Architecture

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

Secondary paths not in the diagram:

- **S3 append-only archive → DuckDB → Neon.** `storage/archive_postings.py` writes
  presence/postings/descriptions to date-partitioned S3 keys that are never
  rewritten; `analytics/build_history.py` queries that Parquet in place with DuckDB
  over httpfs and writes small history tables back into Neon.
- **Postgres → Snowflake and Databricks.** Mirrors for three-engine parity testing.
- **Postgres → Kafka → alerter.** Near-real-time alerting on newly seen postings.
- **FastAPI service on Railway** serving the live agent console via SSE.

### The layers, with their one-line reason for existing

| Layer | Technology | Why this and not something simpler |
|---|---|---|
| Ingestion | Python, 10 ATS adapters, boto3 | Aggregator links are country-gated and unrepairable |
| Data lake | AWS S3, Hive partitioning | Raw fidelity, so a bad downstream decision is reprocessable |
| Curated lake | Parquet, partitioned | Spark cannot read XLSX; columnar scans + partition pruning |
| Warehouse | PostgreSQL (Docker local, Neon hosted) | Free, and the site reads only Neon |
| Lakehouse mirror | Databricks Unity Catalog, Delta tables | Third parity engine; catches type coercion the others hide |
| Second mirror | Snowflake | Catches regex anchoring differences |
| Transformation | dbt (15 models, 113 nodes) | Portable SQL, tested, documented, lineage |
| Batch | PySpark over 595k filings | Partitioned Parquet, windowed aggregates, same job scales |
| Ad-hoc analytics | DuckDB over S3 Parquet | No cluster, no bill, runs inside the daily pipeline |
| Streaming | Kafka (KRaft) | Good internships close in days; daily batch is 22h stale |
| Orchestration | Airflow DAG + GitHub Actions | Actions is what actually runs it; the DAG is the portable definition |
| Enrichment | Claude API, structured outputs | Judgement calls text matching cannot make |
| Outreach agent | Claude tool use + code-enforced rails | Demonstrates the warehouse; refuses rather than invents |
| Service | FastAPI + SSE on Railway | One endpoint, streamed, no queue or scheduler |
| Frontend | React 19, TypeScript strict, Next.js static export, Tailwind | 1,248 prerendered pages, no server |
| Delivery | Vercel (primary), GitHub Pages (data payload) | Both free; see §8 |
| Testing | pytest (529), dbt tests (97), CI with a Postgres service | |

---

## 4. The data pipeline, stage by stage

### 4.1 Board discovery — `ingestion/board_discovery.py` (448 lines)

**The problem:** board coordinates are not derivable from a company name.
Slug-guessing against Greenhouse, Lever and Ashby resolved **5 of the 60 largest
employers**. The other 55 are on Workday or SmartRecruiters where coordinates are
arbitrary strings — Capital One is `capitalone.wd12` + site `Capital_One`, not `wd1`
and not `Careers`; Bosch is `BoschGroup`, not `bosch`.

**The design:** two passes writing to one registry.
1. **Guess** — cheap, automated, one request per slug variant. Good for the startup tail.
2. **Manual** — a careers URL is read, parsed into coordinates, verified, committed
   to `ingestion/boards.yml`. Good for the enterprises holding most of the postings.

**Failures are cached as hard as successes.** A sweep over 4,000 employers that
re-probes what it already answered cannot be resumed and burns a politeness budget
it does not have. Hence `board_registry.status ∈ {resolved, not_found}` — currently
1,627 resolved, 6,689 not found.

### 4.2 Board ingestion — `ingestion/board_ingest.py`, `ingestion/ats/`

Ten adapters (`greenhouse, lever, ashby, workday, smartrecruiters, workable,
rippling, oraclecloud, amazon`, plus `base.py`). Every adapter returns one shape:

```
{board, job_id, title, location, description, url, posted, department}
```

**`url` is the contract.** It must be the employer's own posting, reachable without
a login and without a geo-block. That requirement is the entire reason the package
exists.

**Description length is the second reason.** Board descriptions measure
1,227–8,377 characters against the aggregator's 500-character truncation — and visa
language sits at the *end* of a posting, which is why sponsorship verdicts had
mostly been "unclear".

**Cost control:** Workday and SmartRecruiters charge one extra request *per posting*
for description text, so it is fetched only for postings whose title looks relevant.
Paying it for all registry postings would be tens of thousands of requests to read
text nothing downstream would score.

### 4.3 Aggregator ingestion — `ingestion/adzuna_ingest.py`

Still runs, still useful: it supplies breadth for the market index, where no link is
involved. Writes raw unmodified JSON to S3 partitioned by ingest date. **No
filtering, cleaning or reshaping** — a raw layer's job is fidelity, so a wrong
downstream filter can be corrected by reprocessing rather than by having lost data
never collected.

Its postings are counted in the market index but **never shown as something to
click**, because the links break outside the posting's own country.

### 4.4 Market snapshots — `ingestion/market_snapshot.py`

The compounding asset. For each tracked technology, daily: how many US openings
mention it, which employers hire most for it, how salaries are distributed.

**Why accumulated rather than queried:** Adzuna's `history` endpoint only covers
recognised job categories. Verified against the live API — it returns 12 months for
"data engineer" and *nothing* for "snowflake". No public source publishes
per-technology demand over time, so the only way to have it is to start recording
and keep recording. 38 days held as of 2026-10-07.

> This is also what makes the project hard to copy. Someone who clones the repo gets
> the code. They do not get the history.

### 4.5 DOL visa filings — `ingestion/dol_ingest.py` → `processing/dol_spark.py` → `storage/load_dol.py`

The chain that turns sponsorship from a guess into a record.

- **`dol_ingest.py`** — XLSX → Parquet. Spark cannot read XLSX; the files are 97
  columns wide and mostly irrelevant, so it selects the columns that matter and
  writes Parquet partitioned by fiscal quarter. 576 MB of source XLSX → ~30 MB.
- **`dol_spark.py`** (202 lines) — one row per (employer, fiscal year) with filing
  counts, approval mix, wage percentiles, and the roles they sponsor for. Runs in two
  places from **one definition**: a laptop-sized local session, or Databricks
  serverless. Written to avoid `spark.sparkContext` and RDDs when
  `DATABRICKS_RUNTIME_VERSION` is set, because serverless refuses both.
- **`load_dol.py`** (471 lines) — Spark output back into Postgres, and the matching.

**Matching is the whole difficulty.** The board says "Databricks"; the filing says
"DATABRICKS INC". Both sides are normalised with the **same function** (strip legal
suffixes, punctuation, case) so the join is **exact on a canonical key, never
fuzzy**. A fuzzy match pairing "Apple Inc" with "Big Apple Movers" would put a false
sponsorship claim in front of a stranger — worse than reporting nothing.

**Two cadences, and the fast one was missing.** New DOL quarters land 4×/year, so the
Parquet load is quarterly. But the company→employer mapping must rebuild whenever
the corpus gains employers, which is every morning. It did not: the script appeared
in no workflow, so `company_employer_key` froze on 28 August while board discovery
kept adding companies. **1,226 of 3,821 companies had never been offered to the
matcher at all** — that, not the matching logic, was most of why 58% of companies
showed no sponsorship data.

### 4.6 Human review queue — `storage/review_matches.py`

Nothing inferred from a name alone is published. An exact normalised match is stated
as fact; a multi-word brand prefixing exactly one legal entity is stated as fact;
**everything else is withheld and queued for a person.**

The reason, concretely:
- "Lucid Motors" prefixes LUCID (842 filings), a different company from Lucid Group (15).
- "Cognizant" prefixed four entities; the first version took the alphabetically
  first, claiming 13 filings for a company that has 15,355.

Both are plausible guesses and both are wrong, and a wrong one becomes a sponsorship
claim on a public page read by somebody deciding where to spend an application.

The queue is ordered **by postings at risk**, because that is what a visitor
experiences. Decisions go into `storage/employer_aliases.csv` — the file that
already holds accepted and rejected pairs with a note on why — rather than a second
store that could disagree with the first.

### 4.7 Company canonicalisation — `storage/resolve_companies.py`

The site published **34 duplicate pages**: `databricks` and `databricks-inc`,
`oracle` and `oracle-corporation`, 33 groups in all. Each variant got its own page,
its own peer computation, its own sponsorship row — so a sponsorship section could
appear on one page and "No Department of Labor filings matched this employer" on its
twin.

Worse, three slugs **collided outright**: `/companies/fivetran/` was written for
"FiveTran" (177 postings) then overwritten by "Fivetran" (29). The published page
understated that employer six-fold and `sitemap.xml` listed the URL twice. Nothing
detected it.

The fix reuses `normalise_employer` — the function already canonicalising both sides
of the DOL join — so it is a `GROUP BY`, not an algorithm. Reusing it keeps **one
normaliser in one language**; a second copy in SQL would drift, and the dbt models
must stay portable across engines whose string functions differ.

### 4.8 Technology extraction — `ai_layer/taxonomy.py`, `ai_layer/extract_tech.py`

**The LLM was deliberately removed from this.** Its free-text `tech_stack` output
was unusable: "Analytics" and "analytics" as separate entries; "AI", "AI/ML" and
"AI/LLM" as three different things. You cannot publish a demand index built on that.

A controlled vocabulary of 120 tools with a regex matcher replaced it: cheaper,
deterministic, reproducible. **Published numbers must be re-derivable by anyone, and
free-text model output is not.** The LLM keeps the judgement work it is actually
better at.

`extract_tech.py` materialises matches into `posting_technologies` because dbt
cannot call the Python matcher. Safe to re-run whenever the taxonomy changes — which
is the point: re-running the whole history after adding a technology is how the
index stays consistent.

### 4.9 LLM enrichment — `ai_layer/enrich.py`

One Claude call per posting returns a structured assessment:
- whether a sponsored international student could hold the role (**eligibility**)
- what the posting text says about sponsorship (**sponsorship signal**)
- a **fit score** against a profile
- one-sentence **reasoning** for each

**Eligibility and sponsorship signal are deliberately separated.** The latter
reports *only what the posting states*; the former may draw on well-established
knowledge. Collapsing them would let general knowledge be reported as something the
posting said.

Cost control, in order of impact:
1. One call per posting instead of two (the spec called for two; they read the same
   text and share the same profile context).
2. **Incremental** — a posting is re-scored only when its description changes,
   tracked by `description_hash`. A daily run touches only new postings.
3. **Prompt caching** — profile and instructions are a stable prefix, billed at
   roughly a tenth of the rate after the first call.

### 4.10 Peer companies — `dbt_signal/models/intermediate/int_company_peers.sql`

Jaccard similarity over each company's set of mentioned technologies. **No model
call, no hand-maintained competitor list** — the comparison is published on a
company's own page and sent to people who work there, so it has to be reproducible.

**The floor is the whole point.** Similarity alone is not enough; a company with
three extracted technologies can score 0.43 against something unrelated. Inspecting
the best peer across a spread of companies showed a clean break:

```
Snowflake  -> Databricks        43 shared   0.63
Instacart  -> Stripe            35 shared   0.61
Datadog    -> Scale AI          31 shared   0.61
Ramp       -> Brex              19 shared   0.54
---------------------------------------------------- floor
J.P.Morgan -> NTT America        7 shared   0.64
Google     -> CACI International 4 shared   0.44
```

Below the floor, peers are suppressed entirely — which is why many company pages
have no comparison section. That absence is the feature.

### 4.11 History from the archive — `storage/archive_postings.py` + `analytics/build_history.py`

**The warehouse forgets, in three ways.** `raw_postings` is upserted in place, so
today's `last_seen` overwrites yesterday's and no record survives that a posting was
open on a particular day. `prune.py` deletes rows outright. `export_parquet.py`
rewrites `data.parquet` per partition, so even the curated lake is a snapshot.

The cost showed up on 2026-09-09: the corpus held 9 distinct collection days, both
`MIN_DAYS_FOR_TREND` gates need 45, so **every trend claim on the site and in
outreach was suppressed** — while `market_demand.sql` was computing `pct_change_7d`,
`pct_change_30d` and `has_trend` that nothing could read.

`archive_postings.py` writes three things to date-partitioned S3 keys that are
**never rewritten**:

- `presence/` — (source, job_id) observed on a given day. One row per posting per
  day: the object Neon structurally cannot hold.
- `postings/` — attributes for postings first seen that day. Written once.
- `descriptions/` — text keyed by its sha256, written only when that exact text is new.

`build_history.py` then queries that Parquet **in place with DuckDB over httpfs** —
no load step, no cluster, no bill — and writes tiny history tables back into Neon.
Snowflake was the original plan for this and the trial expired; the work it was going
to do is here instead, and the SQL stays plain so nothing is welded to DuckDB either.

### 4.12 Retention — `storage/prune.py` (516 lines)

**Neon's free tier is 512 MB, and description text was 240 MB of the 489 MB used.**
The pipeline stopped being able to load new postings at all — *"could not extend file
because project size limit has been exceeded"* — which failed the load step and
skipped the seven steps after it.

Two stages, and the second exists because the first does almost nothing:

1. **Delete whole rows**, and only when a posting is *both* older than the retention
   window *and* no longer being seen on any board. A live posting is re-seen on every
   ingest, so **`last_seen` is what separates "delisted" from "still open, just
   posted a while ago"**.
2. That rule alone frees almost nothing on a young corpus: on 2026-09-09 it matched
   **0 postings at 180 days and 13 at 30 days**, because nearly everything is still
   being re-seen every morning. The database sat at 415 MB regardless. So the second
   stage reclaims description text specifically.

A later refinement: **delisted postings are deleted outright rather than having their
text blanked**, and the loader no longer rewrites description text that has not
changed.

`tests/test_prune.py` asserts over the SQL as text rather than behaviourally, because
the dangerous property is *which rows the predicate selects*, and that is readable.

### 4.13 Data quality — `quality/expectations.py`

dbt tests assert things about a **single build**: this column is not null, that range
holds, this grain is unique. They cannot see across runs, so **they never catch the
failure mode that actually matters here — the pipeline running "successfully" while
quietly collecting nothing.**

Four checks dbt structurally cannot do:

- **freshness** — has the daily snapshot stopped? *A gap can never be backfilled,
  because the API only reports today.*
- **drift** — did the corpus suddenly halve, or triple? Either means a source changed
  shape.
- **enrichment** — did LLM scoring silently stop producing usable output?
- **distribution** — did scores collapse to a single value? That is what a broken
  prompt looks like from the outside.

Exits non-zero, so it gates both the Airflow DAG and CI.

### 4.14 Streaming — `streaming/producer.py`, `streaming/alerter.py`

**Why a broker exists in a batch project, stated honestly:** the daily pipeline is
the right shape for building a market index and the wrong shape for *acting* on a
posting. Good internships close in days, and the ranked feed is only as fresh as the
last 07:00 run — **a role posted at 09:00 is invisible for 22 hours.** This path
exists to close that gap and *it is the only reason to add a broker here. It is not
"batch, but with Kafka in front".*

- **Producer** — publishes postings first seen since the last high-water mark, keyed
  by `(source, job_id)` so a partition holds all events for a posting **in order**
  and a replay cannot reorder them. Kafka in KRaft mode (no ZooKeeper).
  `streaming_watermark` holds the mark.
- **Alerter** — **deliberately cheap before it is smart.** Every posting is screened
  with free checks first (title relevance, seniority, verified sponsorship,
  technology overlap) and only survivors reach the LLM. *A consumer that called an
  API for every message would spend money proportional to the firehose rather than to
  the number of genuinely interesting roles — and the firehose is mostly noise.*
  Alerts are written to `posting_alerts` and printed; wiring them to email or a
  webhook is a delivery detail, *the judgement is the part that matters.*

### 4.15 Orchestration — `orchestration/signal_dag.py`

The daily chain as an Airflow DAG. **GitHub Actions is what actually runs in
production**; the DAG is the portable definition and the argument for why the shape
is a DAG rather than `&&`:

- The snapshot and the posting ingest are **independent and run in parallel**.
- Steps have **genuinely different failure modes.** A rate-limited ingest should
  retry with backoff; a failed data-quality check should **stop the run and alert,
  because publishing bad numbers is worse than publishing none.**
- The market snapshot is the **one step that cannot be backfilled** — the API only
  reports today — so it alerts on failure specifically.
- Enrichment **costs money**, so it is capped and depends on everything before it
  succeeding.

Tasks **shell out to the project venv** rather than importing the code, so the DAG
stays independent of the pipeline's dependency tree (Airflow's own constraints are
famously narrow). It has its own virtualenv — see `orchestration/README.md`.

### 4.16 Enrichment evaluation — `eval/run_eval.py` (346 lines)

Scores the LLM layer against a **hand-labelled golden set** (`eval/golden_set.json`).
Run after changing the prompt or switching models, and compare against a previous
run.

It exists because **all three of the model's judgements reach the published board,
the prompt gets edited, and until this existed nothing measured whether an edit made
things better or worse.**

The design point: **it calls `ai_layer.enrich.assess()` directly.** It does not
reimplement the prompt, the schema, or the API call — *a harness that scored its own
copy of the prompt would keep passing while the real one broke.* `--dry-run` costs
nothing; `--compare-to` diffs against a stored result in `eval/results/`.
`tests/test_eval_utils.py` covers the scoring, because the harness is what decides
whether a change was an improvement.

### 4.17 Dashboard — `dashboard/`

A small Streamlit app over the same marts, for exploration rather than publication.
It is why `requirements.txt` exists at all and why it is kept minimal: **Streamlit
Community Cloud installs from it on every deploy**, and the pipeline's heavier
dependencies (dbt, boto3, anthropic) live in `requirements-dev.txt` — *the dashboard
never runs them, and dbt's dependency tree is large enough to slow or break a cloud
build.* `storage/db.py`'s resolution order begins with `DATABASE_URL` precisely
because Streamlit Cloud injects configuration as a single connection URL.

### 4.18 The four requirements files, and why they are separate

| File | For | Why separate |
|---|---|---|
| `requirements.txt` | the Streamlit dashboard only | Community Cloud installs it on every deploy; dbt's tree would slow or break the build |
| `requirements-dev.txt` | the pipeline and CI | the real dependency set |
| `requirements-spark.txt` | PySpark | needs a JVM; most machines running the suite do not have one |
| `requirements-snowflake.txt` | the Snowflake connector | only the parity path needs it |

This split is load-bearing for CI: `tests/test_ci_contract.py` follows first-party
imports **transitively** and fails if a module reachable from a CI-run test imports a
package CI does not install. That test exists because a `fastapi` import in the wrong
module broke CI and nothing else.

---

## 5. The warehouse and dbt

**15 models**, staging → intermediate → marts, plus a dimensional star schema.
113 nodes including 97 tests. 10 declared sources.

```
staging/        stg_jobs                       (view)
intermediate/   int_company_metrics            (view)
                int_company_peers              (table)
                int_company_sponsorship        (table)
marts/          market_demand                  (table)
                tech_cooccurrence              (table)
                tech_demand_history            (table)
                salary_by_tech                 (table)
                ranked_opportunities           (table)
                apply_queue                    (table)
marts/dimensional/
                dim_company, dim_date, dim_technology
                fct_job_posting, fct_market_snapshot
```

### `stg_jobs` — where the two hard things happen

1. **Deduplication.** The same role is posted repeatedly under different `job_id`s
   (Booz Allen's "Data Engineer" in Chantilly appeared 7 times), so identical
   company + title + location collapses to the most recently posted row.
2. **Derived attributes** — seniority, staleness, and whether a salary is a real
   employer-published figure or one of the aggregator's model estimates.

### Portability is written into the SQL

Every model is written to run on **Postgres, Snowflake and Databricks**. The rules,
each learned by hitting it:

- `FILTER (WHERE)` is Postgres-only → counts use `CASE WHEN`.
- The `~*` operator is Postgres-only → use `regexp_like()`.
- Named `WINDOW` clauses are unsupported on Snowflake.
- **Regex anchoring.** Postgres's `regexp_like` searches anywhere; Snowflake's must
  match the whole value. Identical SQL classified **1,590 postings as internships on
  Postgres and 0 on Snowflake** — compiling cleanly on both while silently
  disagreeing. Patterns are wrapped in `.*` deliberately.
- **Word boundaries.** Postgres spells them `\y`; Snowflake has no equivalent (`\b`
  is a backspace in Postgres ARE). Written as explicit `([^a-z]|^)` character
  classes instead — which also stops "internal" matching "intern". (It was matching
  "Internal Auditor" as an internship.)
- `regexp_like` takes **no flags argument** on Spark.
- `'YYYY-MM'` is rejected on Spark, where `YYYY` means the week-based year.
- No format string produces "Jun 2011" on all three engines.
- **Date minus date** is an integer on Postgres and Snowflake and an `INTERVAL DAY`
  on Spark, so `days_since_posted` silently changed type on the third engine and only
  announced itself when something compared it with 60.

`dbt_signal/macros/normalised_title.sql` strips city names out of titles, because
some employers put the city inside the *title*.

### `schema.sql` is the contract

`storage/schema.sql` (440 lines) declares every table. `tests/test_schema_declarations.py`
asserts that **every table a dbt model reads is declared there** — otherwise CI
passes locally and fails on a fresh database.

---

## 6. Three-engine parity — `storage/parity_check.py`

**The thesis:** a build that succeeds on two engines is not a portability test. The
bug this project most fears compiled cleanly on both and returned different answers.
So the artefact worth having is not "it builds" but "**they agree**".

**21 comparisons**, each a single scalar:

- **4 source row counts first** — `raw_postings`, `posting_technologies`,
  `company_employer_link`, `dol_employer_summary`. A stale mirror otherwise fails as
  "the engines disagree", which reads exactly like a dialect bug and is nothing of
  the kind. Establishing that the *inputs* match turns a day of hunting for a dialect
  bug into one line saying the mirror is stale.
- **17 checks** covering the predicates that have actually been wrong before: the
  internship classifier, the sponsorship refusal, the company grain, and the joins
  that carry them into the marts.

A scalar is unambiguous to compare, cheap on a 2X-Small warehouse, and a difference
points at one model rather than a diff of 50,000 rows.

**Why each engine is there:**
- **Snowflake** catches regex anchoring.
- **Databricks** catches type coercion.
- **Postgres** serves the site.

### Each engine is an *oracle* for one class of bug

This is the part worth understanding: the engines are not redundant, they are
chosen because they fail differently. `storage/load_to_databricks.py` states it
directly — *"a portability claim is only worth making if something tests it on an
engine that breaks in a new way."*

| Engine | Oracle for | Why the others cannot catch it |
|---|---|---|
| **Snowflake** | **Regex anchoring** | Its `regexp_like` is implicitly anchored where Postgres searches anywhere. Spark SQL's `rlike` is unanchored *like Postgres*, so Databricks genuinely cannot catch this class. |
| **Databricks** | **Type coercion** | Date minus date is an `INTERVAL DAY` on Spark, an integer on the other two. Also exercises arrays, timestamps with time zones, and format strings. |
| **Postgres** | — | It serves the site, so it is the reference answer. |

Both bug classes have the same dangerous shape: **the SQL compiles on every engine
and the answer is wrong on one.** Nothing fails. That is why parity compares
answers rather than exit codes.

### The Databricks path is genuinely a lakehouse

- **`storage/load_to_databricks.py`** (185 lines) — Postgres → Parquet → Unity
  Catalog Volume → `CREATE OR REPLACE TABLE … AS SELECT * FROM read_files(…)`.
  Those are **managed Delta tables in Unity Catalog**.
  - `CREATE OR REPLACE`, not `INSERT`: *this is a mirror, and a mirror that appends
    is a mirror that double-counts.*
  - Reading the Parquet **in place** means the warehouse does the typing — which is
    the point, because its type choices are exactly what parity is testing.
  - Timestamps are written at **microsecond** precision, not pandas' default
    nanoseconds: Spark reads Parquet timestamps at microseconds and refuses the rest
    outright (`Illegal Parquet type: INT64 (TIMESTAMP(NANOS,true))`). Nothing here
    is measured finer than a second, so the truncation is free.
- **`storage/build_on_databricks.py`** (208 lines) — compiles all 15 dbt models from
  the existing manifest and executes them over REST as
  `CREATE OR REPLACE {VIEW|TABLE} … AS <compiled sql>`. Incremental models are built
  whole: *incrementality is a property of how the warehouse is maintained, not of
  what the SQL means*, and parity is about meaning.
- **`processing/dol_spark.py`** — serverless PySpark reading
  `/Volumes/workspace/signal_dol/lake/parquet`, writing partitioned Parquet back.

**What is honestly not claimed:** no `MERGE`, time travel, `OPTIMIZE`/Z-ORDER,
streaming tables, or Iceberg. Loads are full-refresh. Postgres/Neon is the primary
warehouse; the lakehouse is a parity mirror plus the DOL batch target.

### Why it is not `dbt build --target databricks`

Because that cannot work on this account, and it was tried first.

**Databricks Free Edition does not expose its SQL warehouse over the driver protocol
at all.** A `databricks-sql-connector` connect gets *no response whatsoever* — with
the warehouse awake and the same credentials working over REST. `dbt-databricks`
uses that connector, so it hangs forever with no output. Only the **REST Statement
Execution API** answers. `dbt-databricks` was installed and tried before
`build_on_databricks.py` was written, and it is **not a dependency of anything
here**, because it cannot reach this workspace.

### Five Free Edition restrictions, each found by walking into it

1. No cluster, and no driver protocol — **REST Statement Execution API only**.
2. A job **cannot execute a `python_file` from a Unity Catalog Volume**, even though
   the file is there and readable over the Files API. The script is uploaded to the
   Workspace instead.
3. The job script **must not touch `spark.sparkContext`** — serverless refuses direct
   driver JVM access outright.
4. **No RDDs.** Both this and the previous restriction live in
   `processing/dol_spark.py`, which is written to avoid them when
   `DATABRICKS_RUNTIME_VERSION` is set — so one definition runs locally *and* on
   serverless.
5. **Outbound access is restricted to trusted domains** and it is undocumented
   whether an external S3 bucket is among them, so `scripts/dol_databricks_run.py`
   **pushes** the ~30 MB of Parquet rather than having the cluster pull from AWS.
   (The 576 MB of source XLSX stays local, because Spark cannot read XLSX at all —
   which is why the Parquet conversion step exists in the first place.)

### Snowflake: key-pair auth, because MFA cannot be satisfied by a driver

`storage/snowflake_db.py`. The account enforces multi-factor authentication, and
**MFA is a property of interactive login** — a driver presenting a password is
rejected with *"Multi-factor authentication is required for this account"*, which no
amount of retrying fixes. Key-pair is the supported route for programmatic access.

It is also simply better here: *the password for this account is recoverable from a
chat transcript; the private key never left the machine that generated it and is
gitignored.* Only the public half is registered:

```sql
ALTER USER <user> SET RSA_PUBLIC_KEY='<base64 body>';
```

Resolution order is deliberate so a fresh checkout **fails with a sentence rather
than a stack trace**: `SNOWFLAKE_PRIVATE_KEY_PATH` → `.snowflake_key.p8` beside the
repo → a clear error.

**`scripts/snowflake_key.py` exists because of one specific failure.** The key has to
travel as a GitHub secret, and a PEM is a multi-line value being pasted into a
single-line web form by hand. The first parity run died exactly there: the secret was
present, the key did not parse, and the run failed before reaching Snowflake at all.
A PEM is a header, base64, and a footer — only the line breaks go missing — so rather
than ask for a second manual paste, this reconstructs them. It accepts a correct PEM,
a PEM whose newlines became literal `\n`, a PEM flattened onto one line with or
without spaces, and bare base64 with no header or footer at all. Two tests cover it:
`test_snowflake_key.py` (the shapes) and `test_snowflake_key_parses.py` (does the
result actually parse as a private key — separate because it needs `cryptography`).

`storage/load_to_snowflake.py` copies **only source tables**; every model is rebuilt
by dbt in Snowflake rather than shipped across, *which is what actually proves the
project is not welded to one engine.* Two conversions matter: Postgres `TEXT[]` and
`JSONB` have no pandas equivalent `write_pandas` accepts, so they are serialised to
JSON strings — the models only ever select those columns.

### `storage/mirror_tables.py` — the skipped-model trap

One shared list of what a mirror must carry, imported by both loaders.

**Why it is centralised:** a table missing from a mirror does not fail loudly. The
build reports a Database Error on that source's tests and then **SKIPS every model
downstream**. The first full Snowflake build went **44 passed, 4 errored, 65
skipped** — so almost nothing was verified while the run looked like it had mostly
worked.

> A skipped model is the dangerous outcome, because a failing one announces itself.

Two hand-written copies of the same list is how that happens twice, so the list lives
in one module. `tests/test_mirror_tables.py` asserts every table dbt reads is in it.

---

## 7. The outreach agent

This is the most distinctive part of the project and the part most worth
understanding carefully. It exists in two layers: **deterministic composition** and
**model-written prose that must pass a verifier**.

### 7.1 Insights — `outreach/insights.py` (556 lines)

Produces the opening line: a specific, true, checkable fact about a company's
hiring. **Everything is computed in SQL — no model call** — so the same company
always yields the same numbers and any figure can be re-derived before it is sent.

Three tiers, in increasing order of how hard they are to get anywhere else:

| Tier | Example |
|---|---|
| company | "They have 110 open roles, 63 posted in the last 30 days." |
| peer | "That is roughly 3× the pace of comparable companies." |
| market | "They hire for Spark; demand for it is falling market-wide." |

**Tier 3 is why the market observatory exists.** Anyone can count a company's
postings. Placing that company against the market it hires in requires the daily index.

Rules the generator follows, learned the hard way:
- State counts, shares and comparisons — **never judgements about the recipient's
  performance**. "Your reqs average 41 days open" was tested and dropped.
- Trend claims are gated on 45 days of collection history.
- `rare_tool` insights (`RARE_TOOL_MAX_SHARE = 0.02`, `RARE_TOOL_MIN_POSTINGS = 3`)
  carry **no numbers in their text** at all: "they are one of the few companies in
  this dataset that mention X in job postings at all."

### 7.2 Deterministic composition — `outreach/compose.py` (375 lines)

No model call. Every number is interpolated from an Insight record that came out of
SQL, so **a message cannot contain a figure the warehouse cannot reproduce.**

Three variants, because channels differ: `connection` (LinkedIn note, hard cap 300
chars), `followup` (~120 words), `email` (~105 words).

Fixed structure:
1. Something true and specific about **them**, first — not about the sender.
2. What was built, as one byline, with a link they can check in ten seconds.
3. An ask for their **opinion** rather than their time.

### 7.3 Visitor messages — `outreach/visitor.py` (243 lines)

**The single most instructive bug in the project.** A visitor's message used to be
the owner's message *minus* the owner — fifteen `if _authored(me):` branches in
`compose.py` plus a find-and-replace table rewriting "the companies I track" after
the fact.

**It leaked four separate times**, and each fix was a new branch guarding a new
sentence. The design guaranteed that: every sentence written for the owner was a leak
waiting to be forgotten, and **the flag deciding which voice to use failed OPEN** —
absence of the flag meant "this is the owner", in four separate places
(`tools.py` `sender or _sender()`, `compose.py` ×3, `verify.py` `borrowed=False`
default). A visitor who filled in nothing at all got the owner's email, his school,
and a link to his project.

**The structural fix:** `visitor.py` was written from scratch. It imports only
`CONNECTION_LIMIT, _lead, _para, possessive` from `compose`. It never imports
`candidate_profile`. It never builds a URL. **There is nothing in it to subtract.**
It knows about exactly two things: what the warehouse observed about a company, and
the four fields a visitor typed.

`verify.py`'s `borrowed` is now a **required keyword argument** — the default that
failed open is gone.

A subtlety worth preserving: the words a visitor types are used **exactly as given**.
Wrapping them in "student" turned "Ai Engineer" into "I'm a Ai Engineer student at
Uwaterloo" — wrong article, job title recast as a degree, a description of somebody
the sender is not. The current rule is article-by-head-noun: `"Data Engineer"` →
"I'm a Data Engineer at X"; `"Statistics"` → "I'm **in** Statistics at X", because
"I'm a Statistics at UBC" is not a sentence. `PERSON_NOUNS` is an explicit short
list, deliberately, for the same reason `COUNTABLE` is.

### 7.4 Field mapping — `outreach/fields.py`

`categories_for(what_they_do)` maps the free-text "What you do" answer to
`dim_technology.category` sets, deciding **which** fact about the company leads the
email — an ML tool is interesting for one reader and noise for another.

Deliberately a keyword map and nothing cleverer. Asking a model to classify would put
a model call in front of every draft, make the opener non-deterministic, and fail in a
way nobody could debug. **A missed keyword costs a less-tailored opening line, not a
wrong one** — no match means no preference, and the rarest tool at the company leads
instead. That is a good failure, and it is the common one.

### 7.5 The agent loop — `agent/agent.py` (489 lines)

One job at a time, sequentially. Per job the model gets three tools and the posting;
the expected path is three calls — check, draft, update.

> **The model chooses; the code decides whether each choice is allowed.**

Every rail is enforced in `agent/agent.py` or `agent/tools.py`, **never by
instruction alone**. The prompt asks the model to check status first, *and* the draft
tool independently refuses if the posting is already drafted — so a model that
ignores the instruction gets the same answer. The posture throughout is that the
model may be wrong or adversarial, and the system should be uninteresting either way.

**Nothing sends.** The only status this can write is `email_drafted`.

### 7.6 The three tools

| File | Checks |
|---|---|
| `agent/schemas.py` (204) | **Shape** — required arguments, types, no invented arguments. Pure functions, no I/O, so every rail is testable without a database or a key. |
| `agent/tools.py` (264) | **Truth** — that the posting exists, that the company the model named is the company on file, that the status change is one the agent may make. |

> The design rule: anything that should come from the database is fetched from the
> database, and anything the model supplies that could have come from the database is
> checked against it and rejected on mismatch. **A model argument is treated as a
> claim, never as a fact.**

No tool raises on bad input — a rejected call is a normal outcome that gets logged
and fed back to the model.

Tools: `check_application_status`, `draft_outreach_email`, `update_tracker_status`.

### 7.7 Grounded drafting and verification — `agent/draft.py`, `agent/verify.py`

`compose.py` guarantees every figure traces to a query. Letting a model write the
prose buys better sentences and throws that guarantee away — **unless something
checks.**

```
insights (from SQL)  ->  model writes three variants
                     ->  verifier proves every number, the link, the naming,
                         the channel budgets and the playbook prohibitions
                     ->  pass: use the model's words
                     ->  fail: log the exact offending claim, use compose.py
```

So the **worst case is the old behaviour**, and the log records how often the model
fabricated — a number worth having rather than a thing to hope about.

`verify.py` **fails closed**. It does not ask whether the draft reads well; it asks
whether every checkable claim traces back to an insight.

**What it honestly cannot do, stated plainly because the distinction matters:** it
proves every number in the text appears in the source records. It cannot prove the
sentence *around* the number is a fair reading of it — "hiring has collapsed to 12
roles" and "hiring is up to 12 roles" both verify.

One rail added late: a `RATIO` regex rejecting `\d+%`, `\d+x the/their/your`, and
`\d+ times the`. After ratio insights were removed for visitors, the model computed
its own — "58% of your 465 total openings" — from two numbers it was legitimately
given.

### 7.8 Event wording — `agent/events.py`

Turns a tool call into a sentence a non-technical visitor understands. It is
**deliberately the only place** that does so: the recorded scenarios on the site and
the live service both read from here, so wording cannot drift between a replay and a
live run. It adds nothing and decides nothing — every field it reads was already in
the log.

---

## 8. The public service — `service/`

`service/app.py` (357 lines). One agent run, streamed to a browser as SSE.

**Deliberately small.** One endpoint that does work, one that reads counters, one
liveness check. No background jobs, no scheduler, no queue, no cache, no state beyond
two tables. *The things that break are the things that run, and a page that must never
look broken is best served by a service with very little in it.*

**Every failure here is the same failure to a visitor.** The page gives it 8 seconds
to first byte and falls back to a recorded run on anything else — a timeout, a 502
while deploying, the budget ceiling, a dropped stream halfway through. That is why
`app.py` can afford to be strict about refusing work: **refusing is invisible.**

### Endpoints

- `POST /api/agent/run` — one run, streamed as SSE
- `GET /api/agent/stats` — counters; returns `available: false` rather than an error
  when the database is unreachable
- liveness check

SSE frames include keepalive comments (`: keepalive\n\n`) to defeat proxy idle timeouts.

### Graceful degradation, in layers

The planner (the model call that decides tool order) is a dependency like any other,
and it can be unreachable while the warehouse, rails, verifier and drafting model are
fine. When that happened, the visitor was told the run "could not be completed" —
true of the planner and false of everything needed to write their email. Now it falls
back to `run_fixed_sequence` with a note.

**But only when nothing has run yet.** A failure partway through has already written
a tracker row, and starting over would either duplicate work or trip the duplicate
rail against this run's own writes — so a partial failure is still reported as one.

### `service/sandbox.py` — database-enforced isolation

Public traffic must not touch the real application tracker. Two mechanisms, neither a
code convention a bug could step around:

1. A **`demo` schema** holding its own copy of `outreach_tracker`. A connection
   setting `search_path TO demo, public` resolves `outreach_tracker` to the sandbox
   copy, while `raw_postings`, `apply_queue` and `dim_company` still resolve from
   `public` — because `demo` does not contain them. **The agent's SQL is unchanged.**
2. A **`signal_demo` role** with SELECT on the warehouse and write access to exactly
   one table. If the web layer had a bug aiming a write at the real tracker, Postgres
   refuses it. *The isolation is enforced by the database rather than by the care of
   whoever edits the service next.*

### `service/limits.py` — not becoming an open wallet

Two controls, and **only the second is real protection**:
- A per-visitor cap (20/day) is polite and stops casual repetition; anyone determined
  rotates addresses in a loop. *(It was 5/day until a campus NAT counted as one visitor.)*
- A **daily spend ceiling**, kept in the database rather than in the process — a
  counter that forgets on restart is not a budget, and a redeploy is exactly when
  someone would be hammering it.

Both return a *reason* rather than raising, because the page has to say something
friendly.

### `service/leak.py` — the last check

Four separate upstream fixes — the template, the model prompt, the verifier, the
recorded example — were each believed complete, and **what they had in common was
that nothing inspected the finished message on its way out.** This does, whichever
path wrote it. `OwnerTextLeaked`, `owner_traces()`.

It is deliberately dumb, and deliberately narrow: it matches only `SITE_URL` and
`AUTHORSHIP_CLAIMS`. School and program were removed from the markers, because a
*visitor* saying "I'm a Management Engineering student at Waterloo" is their own
sentence, not a leak.

It lives in its own module and not in `app.py` **because `app.py` imports FastAPI,
which CI does not install** — a test reaching this through `app.py` would fail in CI
and nowhere else. Same trap `clean_sender` was moved out of (→ `service/sender.py`).

### `service/resolve.py` — pasted links

Turns a pasted posting, URL, or bare company name into a company the agent can say
something about. When the employer is not tracked, it **says so and offers the
nearest ones** — a better outcome than a fabricated message, and itself the system
refusing to invent something.

---

## 9. The frontend — `web/`

React 19, TypeScript `strict`, Next.js 15 static export, Tailwind. 1,248 prerendered
pages, no server, no client-side data fetching beyond the search payload.

**It was a re-skin, not a redesign.** The governing constraint was stated plainly:
*nothing functional changes — no new errors, same behaviour, only the appearance.*
That decided the architecture: the 1,248 URLs still resolve, the search predicate is
byte-identical, and the SSE contract with `service/app.py` is untouched.

### Why Next static export and not Vite

Each company page is real HTML today with its own `<title>`, meta and canonical, and
that is how it is indexed. A Vite SPA serves one empty shell; adding a prerender step
rebuilds what Next already does. `output: 'export'` + `generateStaticParams` emits
the same files to the same host with no server. `basePath` and `trailingSlash: true`
reproduce the URL shape exactly.

### The data layer did not move

`site/queries.py` is 17 SQL statements that already know Neon's quirks, the
20,000-row cap, and the dictionary-encoded search payload. Rewriting them in Node
would re-litigate solved problems in a second language where they could then disagree
with the first. **Python keeps the warehouse and gains one job: write JSON**
(`site/export_data.py` → `web/data/*.json`). Next reads JSON at build time and knows
nothing about Postgres.

### Structure

```
web/app/          routes: /, /market, /companies, /companies/[slug],
                  /tech, /tech/[slug], /agent, sitemap.ts, robots.ts
web/components/   JobBoard, AgentConsole, Nav, Footer, Sparkline,
                  ThemeProvider, ThemeToggle
web/components/ui/  Badge, Button, Card, Chip, Field, Meter, Page, Table
web/hooks/        useJobSearch, useAgentStream      <- all behaviour
web/lib/          search, site, data, format, cx
web/scripts/      parity.mjs, redirect-stubs.mjs
web/tailwind.config.ts   every design token, defined once
```

**Components render; hooks decide.** `useJobSearch` and `useAgentStream` hold every
rule, because the rules were *ported* rather than invented — keeping them in one hook
means the next person changing the layout cannot accidentally change what the page returns.

### Search parity, under test

`web/lib/search.ts` is a **line-by-line port** of `site/static/search.js`, not a
rewrite from a description of it, because several of its rules are deliberate and
would look like bugs to anyone reading only the new file.

The contract preserved exactly: all-of substring on title+company; any-of on tech
slugs; the `REMOTE = "\u0000remote"` sentinel; `sponsor="stated"` meaning
`offered_in_posting` *only*; `sponsor="open"` excluding `blocked` and
`no_sponsorship_this_role`; URL params `q, skills, country, level, visa, salary,
days, paid, where` (public and shareable); 110 ms debounce; 100 then +200 paging; and
the summary's *different* sponsor values (`frequent_sponsor|has_sponsored`).

`web/scripts/parity.mjs` reads the **original off disk**, lifts its `decode()`/`keep()`
out into a function, and compares the real implementation over the real payload across
~23 filter combinations on 20,000 rows. Verified identical. *A port checked against a
description of the rules would confirm the description, not the behaviour.*

### The payload is dictionary-encoded

4 MB over 20,000 rows, with company/state/seniority as dictionary indices to cut the
parsed heap on a phone. **Decoding happens once on load**, so every filter compares
plain strings — doing it per comparison would move the cost into the keystroke path,
where it is felt.

### Redirect stubs

`web/scripts/redirect-stubs.mjs` writes 157 stubs for renamed/merged companies
straight into `out/` after the export, **not as Next routes**. A retired slug is not
a page: it has no content, must not be indexed, and exists only so a saved link lands
somewhere useful. Making it a route would put it in the sitemap and give it a real
`<title>` — the opposite of what it is for.

### Design system — every token, and why

Defined in `web/tailwind.config.ts` (190 lines) and `web/app/globals.css` (138
lines), **and nowhere else**. Two rules the config exists to enforce:

1. **No component hard-codes a colour, size, radius or duration.** If a value is
   needed and is not in the config, the answer is to add it there — *that is how a
   "system" becomes eleven slightly different greys.*
2. **Tailwind's own scales are REPLACED, not extended**, for colour, type, spacing,
   radius and shadow. Extending leaves `gray-50` and `text-2xl` reachable, *and the
   moment they are reachable they get used.*

#### Colour — near-monochrome, one accent, status kept separate

Every colour is a CSS variable holding a space-separated RGB triple (not hex), which
is what lets Tailwind's `/<alpha-value>` work — `bg-accent/10` needs channels.

| Token | Dark (default) | Light |
|---|---|---|
| `bg` | `#0B0D0F` | `#FAFBFC` |
| `surface` (recessed: hover, section grounds) | `#131619` | `#F1F3F5` |
| `raised` (inputs, buttons, chips) | `#1A1E22` | `#FFFFFF` |
| `line` | `#23282D` | `#E4E7EA` |
| `line-strong` | `#333A41` | `#CBD1D6` |
| `text` | `#E8EBED` | `#12161A` |
| `text-2` | `#98A1A9` | `#5A636B` |
| `text-3` | `#667079` | `#858E96` |
| `accent` | `#3DDC91` | `#0F9D63` |
| `warn` | `#E0A33E` | `#8A6516` |
| `stop` | `#FF6B5A` | `#C0392B` |

- **Dark is the default** because the site was dark already and because this is a
  tool people keep open.
- **Light is a real design, not an inversion.** The accent darkens to `#0F9D63`
  because `#3DDC91` on white is unreadable, and the greys warm slightly so a white
  page does not glare.
- **`bg` is off-white, not `#FFF`** — and this was a bug fix. Pure white left nothing
  above it: `raised` is what every input, button and chip sits on, and at `#FFFFFF`
  on a `#FFFFFF` page it was **not raised at all** — only a border held those
  controls together, while the dark theme had three real steps. A page a shade below
  white restores the step. The cast is very slightly cool, matching the dark ground
  rather than warming in a different direction.
- **Status colour is separate from the accent.** Green means "this step passed", not
  "this is interactive". Conflating them makes a console unreadable.
- **The console keeps `#0A0C10` in both themes.** A terminal that turns white stops
  reading as a terminal, and this panel's whole job is to look like the real thing.

Three theme states are handled, not two: `:root` carries the dark palette;
`@media (prefers-color-scheme: light)` guarded as `:root:not([data-theme="dark"])`
handles the un-stamped system default; `:root[data-theme="light"]` lets the toggle
win. *A light OS with the toggle set to dark stays dark.* `ThemeProvider` writes the
attribute; `color-scheme` is set in each branch so form controls follow.

#### Type — Newsreader / Archivo / IBM Plex Mono

A serif display over a neutral grotesk, with mono reserved for **every figure**. It
suits a product whose entire claim is that every number traces to a query: it reads
like a publication rather than a dashboard. **Serif is confined to display sizes** so
dense tables stay scannable — which is what most of this site is.

| Step | Size | Line-height | Tracking | Job |
|---|---|---|---|---|
| `display-1` | 4rem | 0.98 | −0.035em | the one headline |
| `display-2` | 2.75rem | 1.02 | −0.03em | section openers |
| `h1` | 2rem | 1.1 | −0.025em | page titles |
| `h2` | 1.375rem | 1.2 | −0.02em | |
| `h3` | 1.0625rem | 1.35 | −0.01em | |
| `body` | 0.9375rem | 1.6 | 0 | |
| `small` | 0.84375rem | 1.5 | 0 | |
| `label` | 0.6875rem | 1.3 | **+0.12em** | uppercase section labels, column heads |
| `micro` | 0.75rem | 1.4 | +0.02em | |
| `data` | 0.8125rem | 1.4 | −0.005em | mono figures in tables |
| `figure` | 1.75rem | 1 | −0.03em | a figure that *is* the sentence |

Two notes that are load-bearing:

- **The range is deliberately wide.** The first version of this scale topped out at
  26px and bottomed at 13px — *a page whose largest text is 26px and whose smallest
  is 13px has no hierarchy, only sizes*, and the result read as the same site in a
  new font. That is literally what happened on the first pass, and the feedback was
  "it looks the same".
- **`label` tracking is the site's main structural device.** +0.12em is what makes
  small caps legible rather than cramped, and the uppercase label is what replaced
  the eyebrow badges that the brief forbade.

Fonts are **self-hosted by `next/font` at build time**, not fetched from Google. The
old site loaded IBM Plex Mono from `fonts.googleapis.com`, costing a DNS lookup, a
TLS handshake and a render-blocking third-party round trip before any text appeared.

#### Space, shape, depth, motion

- **Spacing: a 4px grid, named 1–9** (4, 8, 12, 16, 24, 32, 48, 64, 96). Replacing
  Tailwind's scale means `p-4` is 16px here *and there is no `p-3.5` to reach for at
  2am.*
- **Radius:** `sm` 4px, `md` 6px, `lg` 10px, `full`. **Border width:** 1px default.
- **Shadow: three steps, all restrained**, plus a `focus` ring. *Depth on this site
  comes from the border and the surface, not from a drop shadow.*
- **Width:** `page` 1180px — matched to the old site's `.wrap` so **line lengths do
  not move**; `prose` 720px.
- **Motion: everything under 200ms, one easing curve** (`cubic-bezier(.2,.6,.2,1)`),
  on **hover and focus only**. There is no scroll-triggered animation anywhere.
  Durations are **named, not `DEFAULT`** — a `DEFAULT` key generates a bare
  `duration` class, which reads as "unset" at a call site rather than as a deliberate
  160ms. Two utilities (`.transition-base`, `.transition-fast`) are written once so a
  component cannot invent its own timing.
- Two keyframes only: `blink` (the console cursor) and `rise` (a log line arriving).
- `prefers-reduced-motion: reduce` collapses every animation and transition to
  0.01ms.
- One focus treatment for the whole site, **keyboard only** — a mouse click should
  not leave a ring behind it.
- `.tabular` (`font-variant-numeric: tabular-nums`) because figures line up in
  columns everywhere here.

#### The brief's avoid-list, and the fact that it was violated first

The design brief carried an explicit avoid-list. The first React pass **broke three
items of it**, and all three were fixed:

| Rule | What happened |
|---|---|
| No pill/badge above headlines | An eyebrow badge was placed above *every* headline. Removed; the uppercase `label` step does that job instead. |
| No glassmorphism | `backdrop-blur` was on the nav. Removed. |
| Design mobile deliberately at ~390px | It was never tested there. The 44px serif headline measured **~396px** — wider than the screen — so `display-1` only applies from `lg`, and the headline breaks across three deliberate lines rather than wrapping mid-phrase, *because a headline that wraps mid-phrase reads as a mistake.* |

Also honoured: no scroll-triggered animation, no icon set (the site has none and
does not need one), no centred-everything layout — the hero is **asymmetric**, claim
on the left and the figure that backs it on the right, *because centring both is the
pattern every landing page uses and it says nothing about which of the two is the
point.*

#### Component library — `web/components/ui/`

`Badge` (carrying the old `.tag.ok|no|na|wa` semantics), `Button`, `Card` + `KpiTile`,
`Chip`, `Field` (`Input`, `Select`, `Textarea`), `Meter`, `Page` (+ `PageHeader`,
`Section`, `Kpis`, `Note`), `Table` (+ `Th`, `Td` with a `numeric` variant that is
mono, tabular and right-aligned). Every page composes these; **no page styles a
button.**

Row interaction on the job board is worth noting: the whole row is the target, and
the hover state is **an accent rule scaling in from the left**, not the row lifting
or glowing.

---

## 10. Scheduling, CI and deployment

### GitHub Actions

| Workflow | Schedule | What it does |
|---|---|---|
| `ci.yml` | every push + PR | lint, 529 unit tests, step-wrapper tests, dbt build against a Postgres service with a seeded fixture, behavioural assertions |
| `daily.yml` (580 lines) | 07:00 and 19:00 UTC | the whole pipeline, 26 named steps |
| `site.yml` | 13:30 UTC + push | build and deploy the site |
| `parity.yml` | Mondays 06:00 UTC | three-engine parity |
| `dol.yml` | quarterly, 4th of Jan/Apr/Jul/Oct | DOL refresh |

`daily.yml` step order: secrets present → warehouse reachable → apply schema →
market snapshot → ingest (aggregator, boards) → discover new boards → make room →
load both sources → **archive to the lake** → extract technologies → build history →
canonicalise company names → **rebuild the sponsorship mapping** → derive dbt
credentials → install dbt packages → transform → score new roles → refresh marts →
data quality checks → export curated Parquet → warn if the Databricks token is near
expiry → summarise → **decide the run outcome**.

That last step exists because **fourteen scheduled runs had failed and nothing said
so** — see `tests/test_daily_gate.py`.

### Hosting

| Thing | Where | Notes |
|---|---|---|
| Site (primary) | **Vercel** — `signal-jobsite.vercel.app` | `web/vercel.json` sets `NEXT_PUBLIC_BASE_PATH=""` and `NEXT_PUBLIC_AGENT_API` |
| Site (secondary) | **GitHub Pages** — `zohaiba365.github.io/Signal` | still built by `site/build.py`; **serves the 4 MB `jobs.json`** |
| Agent service | **Railway**, from `service/Dockerfile` | `AGENT_ALLOWED_ORIGINS` must name the serving origin |
| Warehouse | **Neon** (hosted Postgres) | |
| Lake | **AWS S3** | |
| Lakehouse | **Databricks Free Edition** | |
| Dashboard | **Streamlit Community Cloud** | `requirements.txt` exists only for this |

**Important coupling:** the Vercel build does *not* embed the search payload. It is
rebuilt by the daily pipeline and fetched at runtime from the GitHub Pages copy
(`web/app/page.tsx`), so the repo carries no 4 MB file that changes every night and a
build from last week still shows today's roles. CORS is open on that path.
**Consequence: the GitHub Pages deploy must stay alive for the Vercel job board to
populate.**

### Secrets model — read this before touching config

- `.env`, `.railway-env`, `github-secrets.txt`, `.snowflake_key.p8`,
  `.snowflake_key.pub`, `drafts.html` are **gitignored and verified never committed**.
- `dbt_signal/profiles.yml` uses `env_var` with **no defaults, deliberately**, so no
  password can enter git. **Preserve this.**
- Every Anthropic client is constructed from a `.strip()`ed key, asserted by
  `tests/test_api_key_hygiene.py` as an AST check over the source. A key with a
  trailing newline raises `APIConnectionError`, which reads as "the network is down"
  and sent a debugging session in the wrong direction.

---

## 11. Data honesty — the rules the published pages follow

These are product rules, not implementation details. They are the project's actual
thesis and should not be relaxed casually.

1. **Two demand series exist and they are not equally trustworthy.**
   - `market_demand` comes from the market-wide index. Trustworthy.
   - `tech_demand_history` is reconstructed from postings actually collected. It is a
     **biased sample** — roles that filled quickly have disappeared, so older months
     over-represent slow-to-fill and evergreen postings. Shown only for months with a
     usable sample, and **never** presented as market-wide demand.

2. **Salary figures come from the market-wide histogram**, never from per-posting
   salary fields: roughly **99% of those are model estimates** rather than
   employer-published figures.

3. **The job list shows only postings whose link was verified to reach the employer.**
   Everything else still counts toward the market index, where no link is involved.

4. **Peer comparisons state their baseline**, and a company is excluded from the
   baseline it is measured against.

5. **Absence of a DOL match is weak evidence** and the page says so: employers file
   under legal entity names that do not always resolve to the name on a job posting.

6. **Nothing rounds in the flattering direction.** `certified_pct` prints as given —
   99.9% is shown as 99.9%, because rounding it to 100% overstates an approval rate.

7. **Sections do not render when the evidence is thin, and that is the point** rather
   than an oversight. A peer comparison with no peers, or a sponsorship claim with no
   matched filings, would be a false statement on a page the employer may read.

8. **Trend claims are gated on 45 days of collection history.**

---

## 12. Engineering notes — what went wrong, and what fixed it

These are the most useful pages of this document. Several are subtle and would
otherwise be reintroduced.

### Published numbers that were false

- **Ranking at the wrong grain.** Marts ranked at *posting* grain, so one BAE Systems
  internship posted across twelve cities occupied the entire top twelve. Corrected to
  one row per distinct role. A second variant appeared later: some employers put the
  city inside the *title*, so a `normalised_title` macro strips it.

- **"Python demand fell 70%."** Months older than ~5 held only 45–241 postings, where
  one posting swings a share by a point — compounded by survivorship bias, since an
  old posting only appears if it is *still listed*. Threshold raised from 50 to 500.

- **The same error, nearly shipped twice more.** A company page rendered "Google's
  hiring is up 2600%", and the identical claim was the highest-ranked outreach
  insight — the sentence that would have opened a cold email to Google. Job boards
  delist filled roles, so a freshly collected corpus always shows more recent postings
  than older ones; corpus-wide the artifact reads as 1.9×. Month-over-month claims are
  now gated on 45 days of collection history.

- **`certified_pct` rounded 99.9% → 100%.** Caught by comparing the React build's
  figures against the live Jinja pages.

### Data sources that were not what they looked like

- **An aggregator wearing a board's clothing.** Jobgether has a genuine Lever board
  and is not an employer — it republishes other companies' roles under its own name,
  so **1,368 postings arrived with the wrong employer and a link to a middleman**.
  Nothing in the name signals it; such companies have to be listed explicitly.

- **Aggregator links are unrepairable, not broken.** The redirect is country-gated
  (a US posting opened from Canada shows "not available in your region"), the API
  never exposes the employer's own URL, and the redirect returns 403 to anything that
  is not a browser. They can only be replaced.

### Infrastructure failures

- **A load that succeeded into the wrong database.** Three pipeline stages built
  their own connection from `POSTGRES_*` instead of the shared helper, writing to the
  local container while dbt and the site read the hosted warehouse — reporting "6,957
  new, 31,221 total" while the published site served old numbers. In CI, where those
  variables are unset, the same stages failed every run. (This is why `storage/db.py`
  exists and why everything must use it.)

- **Fourteen scheduled runs failed and nothing said so.** Hence the "Decide the run
  outcome" step and `tests/test_daily_gate.py`.

- **352 doomed API calls.** When credit ran out, enrichment ground through 352
  identical failures logging an opaque "API error 400". It now logs the real message
  and aborts after three consecutive account errors.

- **API key with a trailing newline → `APIConnectionError`**, which reads as the
  network being down. `.strip()` at all four client construction sites, plus an AST
  test.

- **CI failed on a `fastapi` import.** `clean_sender` lived in a module that imported
  FastAPI, which CI does not install. Moved to `service/sender.py`; the contract test
  was strengthened to follow first-party imports **transitively**.

- **GitHub Pages `CNAME` must live in the published artifact** for Actions-based
  deploys, not just in the repo.

- **Vercel: `routes-manifest.json` not found.** `vercel.json` set
  `outputDirectory: "out"` while also declaring the Next framework. Removing the
  override fixed it.

- **`next@15.5.4` CVE-2025-66478** → upgraded to 15.5.25; needed a clean `.next`.

- **Parity reported 21 disagreements when Databricks was simply unreachable.**
  `except SystemExit` did not catch `requests.HTTPError`; the workspace was
  `INACTIVE` (`DENY_NEW_AND_EXISTING_RESOURCES`). Three fixes in
  `storage/parity_check.py`: catch `Exception`, return `None` when
  `failures == len(out)`, and guard `pg.rollback()` in try/except (it raised
  `InterfaceError` on a dead connection).

### Agent and outreach failures

- **The identity leak, four times.** See §7.3. Root cause: a visitor's message was
  the owner's message minus `if _authored(me)` branches, and the flag **failed open**
  in four places. Fixed structurally, not with a fifth branch.

- **A recorded run showed no email.** `record_scenarios.py` never wrote
  `scenario.draft`, though `agent.js` always supported it.

- **The page auto-played a recording saying "live drafting is unavailable" on
  arrival**, and the preset chips posted `{scenario: id}` with no posting, so they
  always failed. `LIVE_TIMEOUT` raised 2000 → 8000 ms.

- **The model computed its own ratio** after ratio insights were removed — "58% of
  your 465 total openings". Hence the `RATIO` rail.

- **A test I wrote hung the suite** (`tests/test_parity_unreachable.py`). Removed;
  verified against the real dead workspace instead.

### Local-environment hazards (not code problems)

- **The repo lives in iCloud Drive.** Files get evicted, and reads then fail with
  `TimeoutError: [Errno 60]` mid-collection — which looks exactly like a broken test
  suite. `git rev-list` fails with `mmap failed: Operation timed out` for the same
  reason. CI is unaffected (fresh clone). *Moving the repo out of iCloud Drive would
  eliminate a whole class of phantom failure.*

---

## 13. Repository layout

```
ingestion/      ATS adapters (ats/), board discovery + ingest, aggregator,
                market snapshot, DOL XLSX→Parquet, boards.yml registry
storage/        S3→warehouse loader, schema.sql, connection helper, Parquet export,
                S3 archive, mirrors to Snowflake and Databricks, engine parity check,
                retention/prune, company canonicalisation, employer review queue
processing/     PySpark aggregation over DOL visa filings
ai_layer/       taxonomy (120 tools), extraction, LLM enrichment, candidate profile
dbt_signal/     staging → intermediate → marts, star schema, macros, tests
analytics/      DuckDB history rollups over the S3 archive
site/           warehouse → JSON export for the frontend; legacy Jinja generator,
                templates, client-side search.js, agent.js
web/            Next.js frontend — React 19, TypeScript, Tailwind, static export
agent/          the outreach agent: tool loop, verifier rails, recorded runs
service/        FastAPI streaming endpoint, sandbox, limits, leak check, resolve
outreach/       per-company insights, deterministic composition, visitor messages
streaming/      Kafka producer and alerting consumer
orchestration/  Airflow DAG
quality/        expectation checks run in the pipeline
dashboard/      Streamlit views over the same marts
eval/           golden-set evaluation of the enrichment model
scripts/        CI rehearsal, DOL download, credential helpers
tests/          pytest suite (529 tests)
```

---

## 14. Running it

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
cd .. && python site/export_data.py              # warehouse → JSON

cd web && npm install && npm run build           # 1,248 pages → web/out
```

```bash
pytest                                  # 529 tests
CLEAN=1 bash scripts/ci_rehearse.sh     # exactly what CI runs

# three-engine parity
python storage/load_to_snowflake.py && dbt build --target snowflake
python storage/load_to_databricks.py && python storage/build_on_databricks.py
python storage/parity_check.py          # 21 comparisons

# the human review queue
python storage/review_matches.py --status
python storage/review_matches.py --emit
python storage/review_matches.py --apply review_queue.csv
python storage/load_dol.py --rebuild-mapping-only

# the agent
python agent/agent.py --dry-run --limit 3        # stub model, no API, no writes
python agent/agent.py --limit 1                  # one posting, end to end
uvicorn service.app:app --reload                 # the service, locally
```

Enrichment (`python ai_layer/enrich.py --seniority intern entry --linkable --relevant`)
needs an Anthropic key with credit. It is incremental, and `--linkable` restricts it
to postings the site can actually publish — scoring one it cannot show buys nothing.

---

## 15. Testing discipline

529 pytest tests, 97 dbt tests. Two properties are worth copying:

**Tests cover the decisions that were expensive to get right, not the HTTP.** From
`tests/test_boards.py`: *"These cover the decisions that were expensive to get right,
not the HTTP."* `tests/test_matcher.py` (515 lines) is the largest file in the suite
because employer matching is the highest-stakes inference in the project — it ships
with a 149-row labelled fixture (`tests/fixtures/employer_match_labels.csv`) and
asserts 135 of 135 labelled matches recovered.

**Rails are tested against a scripted adversary.** `agent/tests/test_safety_rails.py`
drives the loop with a deliberately misbehaving model.
`agent/tests/test_verification.py` tests the ways a draft can lie.

**CI's own assumptions are tested without CI.** `tests/test_ci_contract.py` (385
lines): *"Two of this project's costliest failures were not bugs in the pipeline"* —
they were a module importing a package CI does not install, and stages bypassing the
shared DB helper. The test follows first-party imports transitively and blocks
CI-absent packages.

**Separate files for tests needing heavy optional deps** —
`test_dol_spark_runtime.py` (needs pyspark), `test_snowflake_key_parses.py` (needs
cryptography) — so the main suite stays runnable everywhere.

### Verification habits used on this project

- **Route parity** — 1,248 URLs diffed between the Jinja build and the Next export. 0 missing, 0 added.
- **Search parity** — 23 filter combinations over 20,000 real rows, identical result sets.
- **Figure comparison** — rendered React pages diffed against the live Jinja pages for
  the same *figures* (not markup). This is what caught the `certified_pct` rounding.
- **CI simulation** — `scripts/ci_rehearse.sh` with CI-absent packages blocked.
- **Both themes at 390 px and 1440 px** on every page type before calling it done.

---

## 16. Conventions — how to write code that fits

Follow these when contributing; the codebase is consistent about them.

1. **Comments explain *why*, with the specific failure that motivated the rule.**
   Not "handle the edge case" but "an unanchored regex matched 1,590 postings as
   internships on Postgres and 0 on Snowflake". Numbers and names, not categories.
   Long block comments at the top of a module are normal here.

2. **Rails live in code, never in a prompt.** If a model must not do something, the
   code refuses it independently of the instruction.

3. **Fail closed, and fail loudly in the right direction.** An unverifiable draft is
   discarded. A missing flag must not mean "trusted".

4. **One normaliser, one language.** Don't reimplement a Python rule in SQL.

5. **Prefer a short explicit list over a clever heuristic** when a wrong guess puts a
   wrong word in front of a person (`COUNTABLE`, `PERSON_NOUNS`, `DETERMINERS`,
   aggregator-company blocklist).

6. **Absence is a valid output.** Suppress a section rather than render a weak claim.

7. **Ported code is checked against the original implementation, not against a
   description of it.**

8. **Prose style in docs and commits:** plain, specific, no marketing. Commit
   messages state what was wrong and what changed, in the first person, often
   leading with the symptom.

9. **`pytest` must be green before pushing**, and `scripts/ci_rehearse.sh` for
   anything touching imports or dependencies. *A local pass is not a CI pass.*

---

## 17. Known issues and outstanding work

| Issue | Severity | Detail |
|---|---|---|
| **Anthropic key on Railway is dead or out of credit** | High — degrades the flagship demo | Every live agent run emits *"The planner is unreachable"* and the draft is template-written. Rails, warehouse and refusals all still work, so output stays correct. Fix: set `ANTHROPIC_API_KEY` in the Railway service's variables. |
| **Vercel `sitemap.xml` and `robots.txt` point at GitHub Pages URLs** | Medium — SEO | `web/app/sitemap.ts` builds from `meta().site_url`, which `site/export_data.py` fills with `SITE_URL` (the Pages URL). All 1,248 `<loc>` entries read `https://zohaiba365.github.io/Signal/…`. Fix: make `SITE_URL` configurable per deploy target. |
| **Databricks workspace is deactivated** | Medium | `denyReason: INACTIVE`. Parity no longer *fails* because of it (it reports unreachable), but the lakehouse cannot run until someone logs into accounts.cloud.databricks.com. |
| **Databricks token expires 2026-10-17** | Medium | `daily.yml` warns when it is near expiry. |
| **Repo lives in iCloud Drive** | Medium — developer experience | Causes `TimeoutError: [Errno 60]` during pytest collection and `mmap failed` in git. Not a code issue; CI is unaffected. Moving the repo out would remove a class of phantom failure. |
| **Two deploys to keep alive** | Low but coupled | Vercel serves the pages; GitHub Pages serves `jobs.json`. If Pages dies, the Vercel job board does not populate. |
| **`signal_demo` database password** | Low | Rotation is optional but was never done. |
| **Next DOL quarterly run** | Scheduled | 2027-01-04 via `dol.yml`. |
| **`site/build.py` (Jinja) still runs** | Low — intentional | Kept so `jobs.json` keeps publishing and so rollback is one line. Retiring it means moving payload publication into the Next pipeline first. |

---

## 18. What the data currently shows (2026-10-07)

Re-derive before quoting — these move weekly.

**The warehouse market is a two-horse race.** Databricks (37.2%, 23,610 openings) and
Snowflake (32.1%, 20,381) hold 69% of warehouse demand between them. BigQuery is
10.9%; Redshift — AWS's own product — is 7.9%.

**Newer tooling pays best.** Share of postings in the top salary band: DuckDB 96%,
ClickHouse 90%, Iceberg 88%, Dagster 87%, SageMaker 85% — against a mid-70s field
for established tools.

**Stacks cluster hard, and the tightest cluster is the oldest one.** 88% of postings
mentioning JCL also mention COBOL — 311× what their volumes alone would predict — and
CICS, Db2, TSO and VSAM sit in the same knot (JCL↔VSAM reaches 558×). The
co-occurrence model rediscovered the mainframe with no prior knowledge that it
exists. At the other end, Dagster and Prefect co-occur at ~305×: direct competitors,
named together because the teams hiring for one are evaluating both.

---

## 19. Quick orientation for an assistant

If you are picking this up cold, read in this order:

1. `README.md` — the public summary, and the figures that are current.
2. `storage/db.py` — how anything reaches the warehouse. Everything must use it.
3. `dbt_signal/models/staging/stg_jobs.sql` — dedup, derived attributes, and the
   portability rules in one file.
4. `outreach/insights.py` then `agent/verify.py` — the honesty machinery.
5. `web/lib/search.ts` and `web/hooks/useJobSearch.ts` — the only load-bearing
   client logic.
6. `web/tailwind.config.ts` and `web/app/globals.css` — the whole design system,
   with the reason for each token in a comment beside it.
7. `.github/workflows/daily.yml` — what actually runs, in order.

Things to be careful about:

- **Do not relax a data-honesty rule** (§11) to make a page look fuller.
- **Do not move a rail into a prompt.**
- **Do not add a default that makes an absent flag mean "trusted".**
- **Do not change search behaviour** without running `node web/scripts/parity.mjs`.
- **Do not add defaults to `dbt_signal/profiles.yml`.**
- **Do not hard-code a colour, size, radius or duration in a component** — add the
  token to `web/tailwind.config.ts`.
- **Do not reach for `dbt-databricks`.** It cannot connect to this workspace at all;
  the REST path in `storage/build_on_databricks.py` is not a workaround to be tidied
  away.
- **Re-derive numbers** rather than copying them from documentation, including this file.
