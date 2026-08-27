# Signal

**An observatory for the US data-engineering job market.**

Signal tracks what the US data and AI job market is actually hiring for — which
technologies are gaining and losing demand, what each skill pays, and which tools
employers ask for together. It ingests job postings daily, cleans and models them
through a warehouse, and layers an LLM on top to assess individual opportunities
against a candidate profile.

It started as a tool for one problem (finding a US internship as a Canadian student)
and became a market intelligence product, because the interesting data turned out to
be the market itself rather than any single posting.

---

## What it does

1. **Ingests** US data/AI job postings from the Adzuna API into an S3 data lake,
   partitioned by date.
2. **Loads** them into Postgres with idempotent upserts, so re-runs and backfills
   are safe.
3. **Transforms** them through dbt — staging → intermediate → marts — deduplicating
   to one row per distinct role.
4. **Extracts** technology mentions using a controlled vocabulary of 104 tools.
5. **Snapshots** market-wide demand for 46 technologies every day, building a time
   series that does not exist anywhere else.
6. **Assesses** each role with Claude: work-authorisation eligibility, sponsorship
   signals, fit against a profile, and the reasoning behind each call.
7. **Serves** two views — a personal ranked opportunity feed, and a public market index.

## Architecture

```
Adzuna API ──► S3 data lake ──► Postgres ──► dbt ──► Streamlit
               (raw JSON,        (idempotent   (staging →      ├─ apply queue
                Hive-            upserts)      intermediate    └─ market index
                partitioned)                   → marts)
                                                    ▲
   taxonomy.py (104 technologies) ──────────────────┤
   Claude API (eligibility, fit, reasoning) ────────┘
```

| Layer | Technology |
|---|---|
| Ingestion | Python, Adzuna API, boto3 |
| Data lake | AWS S3, Hive-style partitioning |
| Warehouse | PostgreSQL (Docker locally, Neon hosted) |
| Transformation | dbt (24 models and tests) |
| Enrichment | Claude API (Opus), structured outputs |
| Delivery | Streamlit |
| Testing | pytest, dbt tests |

## Scale

- **20,084** job postings from **4,400** companies
- **104** technologies in the controlled vocabulary, **46** snapshotted daily
- **14,349** technology mentions extracted
- **24** dbt models and tests, all passing

---

## What the data shows

**The warehouse market is a two-horse race.** Databricks (17,713 openings) and
Snowflake (15,449) together hold 69% of warehouse demand. Redshift — AWS's own
product — sits at 7%.

**Newer tooling pays best.** Share of postings in the top salary band:
DuckDB 95%, ClickHouse 93%, Iceberg 85%, Dagster 85% — against a mid-70s field
for established tools.

**Stacks cluster hard.** 91% of postings mentioning Looker also mention BigQuery
(lift 109×). The co-occurrence model rediscovered the Google, ML, and container
stacks with no prior knowledge of what they are.

---

## How AI was used

**In the product.** The Claude API reads each posting and returns a structured
assessment: whether a sponsored international student could hold the role, what the
text says about visa sponsorship, a fit score against the candidate profile, and
one-sentence reasoning for each. Eligibility is deliberately separated from
sponsorship signal — the latter reports only what the posting text states, while
the former may draw on well-established knowledge (a defence contractor requiring
US citizenship, for instance). Truncated descriptions make that distinction matter.

**Where the LLM was deliberately removed.** Technology extraction originally used
the LLM and produced unusable output — "Analytics" and "analytics" as separate
entries, "AI", "AI/ML" and "AI/LLM" as three different things. It was replaced with
a dictionary matcher, which is cheaper, deterministic, and reproducible. Published
numbers need to be re-derivable by anyone; an LLM's free-text output is not.

**In development.** The pipeline was built with Claude Code, which also surfaced
several bugs documented below.

---

## Engineering notes

Things that went wrong and what fixed them — the parts worth reading:

- **Ranking at the wrong grain.** Marts originally ranked at *posting* grain, so one
  BAE Systems internship posted across twelve cities occupied the entire top twelve.
  Corrected to one row per distinct role, tracking `locations_posted` instead.
  A second variant of the same bug appeared later: some employers put the city inside
  the *title*, so a `normalised_title` macro strips trailing location suffixes.

- **A published number that was false.** The trend model reported "Python demand fell
  70%". Investigation showed months older than ~5 months held only 45–241 postings,
  where a single posting swings a share by a full point — compounded by survivorship
  bias, since an old posting only appears if it is *still listed*. The sample
  threshold was raised from 50 to 500.

- **Useless percentiles.** The salary histogram has seven buckets and the top one is
  open-ended, holding 62% of Python postings, so p25/median/p75 all landed on
  $140,000 for nearly every technology. Replaced with share-of-postings-in-top-band,
  which discriminates properly.

- **A connection held open for 22 hours.** psycopg2 opens a transaction on the first
  SELECT and a cached Streamlit connection never commits, leaving the session idle in
  transaction — pinning vacuum and blocking DDL. Fixed with autocommit for read-only
  connections.

- **352 doomed API calls.** When the API credit balance ran out, enrichment ground
  through 352 identical failures logging an opaque "API error 400". It now logs the
  real message and aborts after three consecutive 4xx account errors.

---

## Running it locally

```bash
git clone https://github.com/ZohaibA365/Signal.git && cd Signal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env        # then fill in your API keys
docker compose up -d        # starts Postgres with the schema applied

python ingestion/adzuna_ingest.py --pages 5   # pull postings into S3
python storage/load_to_warehouse.py           # S3 → Postgres
python ai_layer/extract_tech.py               # technology mentions
python ingestion/market_snapshot.py           # daily market capture

cd dbt_signal && dbt build --profiles-dir .   # build all models
streamlit run dashboard/app.py
```

Run the tests with `pytest`.

Enrichment (`python ai_layer/enrich.py --seniority intern entry`) needs an Anthropic
API key with credit. It costs about $0.0094 per role and is incremental — a posting
is re-scored only when its description changes.

## Repository layout

```
ingestion/      Adzuna postings + daily market snapshot
storage/        S3 → warehouse loader, schema, connection helper
ai_layer/       technology taxonomy, extraction, LLM enrichment, profile
dbt_signal/     staging → intermediate → marts, macros, tests
dashboard/      Streamlit app and the public market index page
tests/          pytest suite
```

## Data honesty

Two demand series exist here and they are not equally trustworthy:

- `market_demand` comes from the job board's market-wide index. Trustworthy.
- `tech_demand_history` is reconstructed from postings actually collected. It is a
  biased sample — roles that filled quickly have disappeared — so it is shown only
  for months with a usable sample size and is never presented as market-wide demand.

Salary figures come from the market-wide histogram, never from per-posting salary
fields: roughly 99% of those are the data source's model estimates rather than
employer-published figures.

---

Built by [Zohaib](https://github.com/ZohaibA365) · Management Engineering, University of Waterloo
