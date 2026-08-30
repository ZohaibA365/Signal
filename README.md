# Signal

**A job board for US and Canadian data roles, built on a warehouse that explains them.**

🔗 **[zohaiba365.github.io/Signal](https://zohaiba365.github.io/Signal/)**

Signal collects data and AI job postings from employers' own career boards, models
them through a warehouse, and publishes two things: a searchable job board where
every link goes straight to the employer, and a market index tracking what the
market is actually hiring for.

It started as one problem — finding a US internship as a Canadian student — and the
market turned out to be more interesting than any single posting.

---

## What it does

1. **Resolves** each employer to its applicant-tracking system, cached so a name is
   answered once. 130 boards resolved across Greenhouse, Lever, Ashby, Workday,
   SmartRecruiters, Workable and Amazon's own API.
2. **Ingests** postings from those boards into an S3 data lake, Hive-partitioned by
   source, country and date.
3. **Loads** them into Postgres with idempotent upserts, so re-runs and backfills are
   safe.
4. **Transforms** through dbt — staging → intermediate → marts, plus a star schema —
   deduplicating to one row per distinct role.
5. **Processes** 800,569 Department of Labor visa filings with PySpark, joining them
   to employers so sponsorship is a matter of record rather than inference.
6. **Extracts** technology mentions with a controlled vocabulary of 120 tools.
7. **Snapshots** market-wide demand daily, accumulating a time series that exists
   nowhere else.
8. **Assesses** roles with Claude: work-authorisation eligibility, sponsorship
   signals, fit against a profile, and the reasoning behind each call.
9. **Publishes** a static site — 640 pages, client-side search, no backend.

## Architecture

```
career boards ─┐
(Greenhouse,   │
 Lever, Ashby, ├─► S3 data lake ──► Postgres ──► dbt ──► static site
 Workday,      │   (raw JSON,       (idempotent  (staging → ├─ job search
 SmartRecruit, │    Hive-           upserts)     marts +    ├─ company pages
 Workable,     │    partitioned)                 star       └─ market index
 Amazon)       │                                 schema)
Adzuna ────────┘                                     ▲
                                                     │
DOL visa filings ──► PySpark ────────────────────────┤
taxonomy.py (120 technologies) ──────────────────────┤
Claude API (eligibility, fit, reasoning) ────────────┘
```

| Layer | Technology |
|---|---|
| Ingestion | Python, 7 ATS adapters, boto3 |
| Data lake | AWS S3, Hive-style partitioning |
| Warehouse | PostgreSQL (Docker local, Neon hosted, Snowflake third target) |
| Transformation | dbt — 15 models, 106 nodes including tests |
| Batch processing | PySpark over 800k visa filings |
| Streaming | Kafka (KRaft), producer + alerting consumer |
| Orchestration | Airflow |
| Enrichment | Claude API (Opus 5), structured outputs |
| Delivery | Static site generator (Jinja2) on GitHub Pages |
| Testing | pytest (43 tests), dbt tests, CI with a Postgres service |

## Scale

- **41,110** postings from **5,052** companies, **18,000** from employers' own boards
- **130** career boards resolved; **555** companies answered and cached
- **92,219** technology mentions across **120** tracked tools
- **108,001** DOL employer records; **1,938** employers with verified filings
- **640** published pages, searchable in the browser over a 433 kB payload

---

## Why career boards, not an aggregator

The job board originally ran on aggregator data and 90% of it was unclickable.

An aggregator redirect is gated by country: opened from Canada, a US posting shows
"not available in your region". The API never exposes the employer's own URL, and
the redirect returns 403 to anything that is not a browser, so it cannot be resolved
either. Those links were not broken — they were unrepairable.

Employers' own boards give the real URL by construction. The cost is that board
coordinates cannot be guessed. Slug-guessing against the startup boards resolved 5
of the 60 largest employers; the rest are on Workday, whose tenant *and* site path
are arbitrary strings — Capital One is `capitalone.wd12` with site `Capital_One`,
not `wd1`, not `Careers`. SmartRecruiters is the same: `BoschGroup`, not `bosch`.
So coordinates are read off a real careers URL once and cached in a registry.

The switch paid twice. Board descriptions run 1,200–8,400 characters against the
aggregator's 500-character truncation — and the visa language that decides
sponsorship sits at the *end* of a posting, which is why those verdicts had mostly
been "unclear".

---

## What the data shows

**The warehouse market is a two-horse race.** Databricks and Snowflake together hold
69% of warehouse demand. Redshift — AWS's own product — sits at 7%.

**Newer tooling pays best.** Share of postings in the top salary band: DuckDB 95%,
ClickHouse 93%, Iceberg 85%, Dagster 85% — against a mid-70s field for established
tools.

**Stacks cluster hard.** 91% of postings mentioning Looker also mention BigQuery
(lift 109×). The co-occurrence model rediscovered the Google, ML and container
stacks with no prior knowledge of what they are.

---

## How AI was used

**In the product.** Claude reads each posting and returns a structured assessment:
whether a sponsored international student could hold the role, what the text says
about sponsorship, a fit score, and one-sentence reasoning for each. Eligibility is
deliberately separated from sponsorship signal — the latter reports only what the
posting states, while the former may draw on well-established knowledge.

**Where the LLM was deliberately removed.** Technology extraction originally used the
LLM and produced unusable output — "Analytics" and "analytics" as separate entries,
"AI", "AI/ML" and "AI/LLM" as three things. A dictionary matcher replaced it:
cheaper, deterministic, reproducible. Published numbers must be re-derivable by
anyone, and free-text model output is not.

Peer companies are derived the same way — Jaccard similarity over technology sets,
no model call — and suppressed entirely below a confidence floor.

---

## Engineering notes

Things that went wrong and what fixed them:

- **Ranking at the wrong grain.** Marts ranked at *posting* grain, so one BAE Systems
  internship posted across twelve cities occupied the entire top twelve. Corrected to
  one row per distinct role. A second variant appeared later: some employers put the
  city inside the *title*, so a `normalised_title` macro strips it.

- **A published number that was false.** The trend model reported "Python demand fell
  70%". Months older than ~5 held only 45–241 postings, where one posting swings a
  share by a point — compounded by survivorship bias, since an old posting only
  appears if it is *still listed*. Threshold raised from 50 to 500.

- **The same error, nearly shipped twice more.** A company page rendered "Google's
  hiring is up 2600%", and the identical claim was the highest-ranked outreach
  insight — the sentence that would have opened a cold email to Google. Job boards
  delist filled roles, so a freshly collected corpus always shows more recent
  postings than older ones; corpus-wide the artifact reads as 1.9×. Month-over-month
  claims are now gated on 45 days of collection history.

- **An aggregator wearing a board's clothing.** Jobgether has a genuine Lever board
  and is not an employer — it republishes other companies' roles under its own name,
  so 1,368 postings arrived with the wrong employer and a link to a middleman.
  Nothing in the name signals it; such companies have to be listed explicitly.

- **A load that succeeded into the wrong database.** Three pipeline stages built
  their own connection from `POSTGRES_*` instead of the shared helper, writing to the
  local container while dbt and the site read the hosted warehouse — reporting
  "6,957 new, 31,221 total" while the published site served old numbers. In CI, where
  those variables are unset, the same stages failed every run.

- **Snowflake portability, three defects silent.** `FILTER (WHERE)`, the `~*`
  operator, and named `WINDOW` clauses are unsupported, and unanchored `regexp_like`
  matched 1,590 internships on Postgres and 0 on Snowflake — the same query, silently
  disagreeing. It was also matching "Internal Auditor" as an internship.

- **352 doomed API calls.** When credit ran out, enrichment ground through 352
  identical failures logging an opaque "API error 400". It now logs the real message
  and aborts after three consecutive account errors.

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
cd .. && python site/build.py                    # generate the static site
```

Run the tests with `pytest`.

Enrichment (`python ai_layer/enrich.py --seniority intern entry --linkable --relevant`)
needs an Anthropic key with credit. It is incremental — a posting is re-scored only
when its description changes — and `--linkable` restricts it to postings the site can
actually publish, since scoring one it cannot show buys nothing.

## Repository layout

```
ingestion/      ATS adapters, board discovery + ingest, aggregator, market snapshot
storage/        S3 → warehouse loader, schema, connection helper, Parquet export
processing/     PySpark aggregation over DOL visa filings
ai_layer/       taxonomy, extraction, LLM enrichment, candidate profiles
dbt_signal/     staging → intermediate → marts, star schema, macros, tests
site/           static site generator, templates, client-side search
outreach/       per-company insights and message drafting
streaming/      Kafka producer and alerting consumer
orchestration/  Airflow DAG
quality/        expectation checks run in the pipeline
tests/          pytest suite
```

## Data honesty

Two demand series exist and they are not equally trustworthy:

- `market_demand` comes from the market-wide index. Trustworthy.
- `tech_demand_history` is reconstructed from postings actually collected. It is a
  biased sample — roles that filled quickly have disappeared — so it is shown only
  for months with a usable sample and never presented as market-wide demand.

Salary figures come from the market-wide histogram, never from per-posting salary
fields: roughly 99% of those are model estimates rather than employer-published
figures.

The job list shows only postings whose link was verified to reach the employer.
Everything else still counts toward the market index, where no link is involved.

Peer comparisons state what their baseline is. It is every tracked company above a
posting floor, not a curated peer set, so the pages say "the companies I track"
rather than "comparable companies" — and a company is excluded from the baseline it
is measured against.

---

Built by [Zohaib](https://github.com/ZohaibA365) · Management Engineering, University of Waterloo
