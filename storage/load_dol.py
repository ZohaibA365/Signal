"""
Load the Spark employer summary into the warehouse.

Bridges the Spark output back into Postgres so dbt models and the dashboard
can join sponsorship facts onto companies. Matching is the whole difficulty:
the job board says "Databricks", the government filing says "DATABRICKS INC",
and unless those resolve to one employer the sponsorship signal is useless.

Both sides are normalised with the SAME function (strip legal suffixes,
punctuation and case) so the join is exact on a canonical key rather than
fuzzy. Exact-on-normalised is deliberate: a fuzzy match that silently pairs
"Apple Inc" with "Big Apple Movers" would put a false sponsorship claim in
front of a stranger, which is worse than reporting nothing.

Two paths, because they have completely different cadences and the faster one
was missing. New DOL quarters land four times a year, so the Parquet load is
quarterly. But the company -> employer mapping has to be rebuilt whenever the
corpus gains employers, which is every morning.

It was not. This script appeared in no workflow, so company_employer_key froze
on 28 August while board discovery kept adding companies: 1,226 of the 3,821
companies in the corpus had never been offered to the matcher at all. That,
not the matching logic, is most of why 58% of companies showed no sponsorship
evidence - "Databricks, Inc." normalises to DATABRICKS, which is in the filing
data with 547 filings, and matches the instant the mapping is rebuilt.

Usage:
    python storage/load_dol.py                       # full: Parquet + mapping
    python storage/load_dol.py --rebuild-mapping-only  # mapping from Postgres
"""

from __future__ import annotations

import argparse
import bisect
import glob
import logging
import os
import sys
from collections import Counter

import pandas as pd
from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestion"))
from db import connect, describe  # noqa: E402
from dol_ingest import normalise_employer  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("load_dol")

SUMMARY_DIR = "data/dol_employer_summary"

DDL = """
CREATE TABLE IF NOT EXISTS dol_employer_summary (
    employer_key     TEXT    NOT NULL,
    fiscal_year      TEXT    NOT NULL,
    employer_name    TEXT,
    filings          INTEGER NOT NULL,
    certified        INTEGER,
    certified_pct    NUMERIC,
    tech_filings     INTEGER,
    tech_pct         NUMERIC,
    distinct_titles  INTEGER,
    distinct_states  INTEGER,
    median_wage      NUMERIC,
    p25_wage         NUMERIC,
    p75_wage         NUMERIC,
    max_wage         NUMERIC,
    tech_soc_titles  TEXT[],
    rank_in_year     INTEGER,
    PRIMARY KEY (employer_key, fiscal_year)
);
CREATE INDEX IF NOT EXISTS idx_dol_employer ON dol_employer_summary (employer_key);

-- Canonical key for every company seen in postings, so the join to filings is
-- exact rather than fuzzy.
CREATE TABLE IF NOT EXISTS company_employer_key (
    company_name TEXT PRIMARY KEY,
    employer_key TEXT,
    -- exact  : normalised names are identical - highest confidence
    -- prefix_strong : multi-word brand name matched a legal entity
    --                 ("Capital One" -> "CAPITAL ONE SERVICES"). Safe.
    -- prefix_weak   : single generic word. May be a different organisation
    --                 entirely; never state as fact.
    match_type   TEXT
);
CREATE INDEX IF NOT EXISTS idx_company_employer_key ON company_employer_key (employer_key);
-- The table predates match_type; CREATE IF NOT EXISTS will not add it.
ALTER TABLE company_employer_key ADD COLUMN IF NOT EXISTS match_type TEXT;

-- Every DOL employer a company could plausibly be, kept so an ambiguous case
-- can be reviewed rather than guessed, and so "why does this company show no
-- sponsorship" is an answerable question.
--
-- It exists because the previous code took hits[0] - lexicographically first -
-- whenever a brand prefixed several legal entities. "Cognizant" resolved to
-- COGNIZANT MOBILITY with 13 filings instead of COGNIZANT TECHNOLOGY SOLUTIONS
-- with 15,274, out of 12 candidates. That only escaped publication because
-- COGNIZANT has no space and so graded prefix_weak; a multi-word brand in the
-- same position would have been stated as fact.
-- Declared here too because build_mapping joins it and load order is not
-- guaranteed on a fresh warehouse. storage/resolve_companies.py owns it.
CREATE TABLE IF NOT EXISTS company_identity (
    company_name   TEXT PRIMARY KEY,
    company_key    TEXT NOT NULL,
    canonical_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_employer_candidates (
    company_name TEXT    NOT NULL,
    employer_key TEXT    NOT NULL,
    filings      INTEGER,
    PRIMARY KEY (company_name, employer_key)
);
"""

COLUMNS = ["employer_key", "fiscal_year", "employer_name", "filings", "certified",
           "certified_pct", "tech_filings", "tech_pct", "distinct_titles",
           "distinct_states", "median_wage", "p25_wage", "p75_wage", "max_wage",
           "tech_soc_titles", "rank_in_year"]


def build_mapping(cur) -> None:
    """
    Rebuild company_employer_key from whatever is already in the warehouse.

    Reads dol_employer_summary rather than the Parquet, so this runs daily in
    about twenty seconds with no local data and no new dependency.
    """
    # Keyed on the canonical name, because that is what stg_jobs.sql projects
    # and therefore what every downstream model joins on. Two reasons it is not
    # the raw name: 16 rows carried an untrimmed one that nothing could match,
    # and since company_identity collapses "Databricks" into "Databricks, Inc."
    # a mapping keyed on raw spellings would simply miss the 184 companies that
    # were canonicalised. The coalesce mirrors stg_jobs exactly - if those two
    # expressions ever diverge, sponsorship silently stops joining.
    cur.execute("""
        SELECT DISTINCT
               coalesce(ci.canonical_name, nullif(trim(r.company_name), ''))
        FROM raw_postings r
        LEFT JOIN company_identity ci
               ON ci.company_name = nullif(trim(r.company_name), '')
        WHERE r.company_name IS NOT NULL
    """)
    companies = [c[0] for c in cur.fetchall() if c[0]]

    cur.execute("SELECT employer_key, sum(filings) FROM dol_employer_summary GROUP BY 1")
    filings_by_key = dict(cur.fetchall())
    dol_keys = set(filings_by_key)
    # Sorted once so prefix lookups do not rescan the whole set per company.
    sorted_keys = sorted(dol_keys)

    mapping, candidates = [], []
    for company in companies:
        key = normalise_employer(company)
        if not key:
            mapping.append((company, None, None))
            continue
        if key in dol_keys:
            mapping.append((company, key, "exact"))
            continue
        # Prefix fallback: DOL files under legal entity names while job boards
        # use brand names. Require a word boundary and a reasonably specific
        # stem - a short key like "APPLE" would otherwise match "APPLE MOVERS"
        # and put a false sponsorship claim in an email.
        if len(key) >= 8:
            i = bisect.bisect_left(sorted_keys, key)
            hits = []
            while i < len(sorted_keys) and sorted_keys[i].startswith(key):
                if sorted_keys[i] == key or sorted_keys[i][len(key):len(key) + 1] == " ":
                    hits.append(sorted_keys[i])
                i += 1
            if hits:
                candidates += [(company, h, filings_by_key.get(h)) for h in hits]
                if len(hits) > 1:
                    # Ambiguous, so decide nothing. Recording a guess here is
                    # what produced the Cognizant error, and filing volume is
                    # not a tiebreak either: "Lucid Motors" prefixes LUCID with
                    # 842 filings, which is a different company from Lucid
                    # Group with 15. Volume may order a review queue. It may
                    # never pick a winner.
                    mapping.append((company, None, "prefix_ambiguous"))
                    continue
                # Confidence depends on how distinctive the brand name is.
                # "CAPITAL ONE" -> "CAPITAL ONE SERVICES" is safe. A single
                # generic word is not: sampling found "Lighthouse" ->
                # "LIGHTHOUSE BEHAVIORAL SOLUTIONS" and "Invictus" ->
                # "INVICTUS ACADEMY OF RICHMOND", different organisations
                # entirely. The weak case is labelled so nothing downstream
                # states it as fact in front of a stranger.
                strong = " " in key
                mapping.append((company, hits[0],
                                "prefix_strong" if strong else "prefix_weak"))
                continue
        mapping.append((company, None, None))

    cur.execute("TRUNCATE company_employer_key")
    execute_values(cur,
                   "INSERT INTO company_employer_key "
                   "(company_name, employer_key, match_type) VALUES %s",
                   mapping, page_size=1000)
    cur.execute("TRUNCATE company_employer_candidates")
    if candidates:
        execute_values(cur,
                       "INSERT INTO company_employer_candidates "
                       "(company_name, employer_key, filings) VALUES %s",
                       candidates, page_size=1000)

    kinds = Counter(m[2] for m in mapping if m[2])
    log.info("Mapped %s companies: %s exact, %s strong prefix, %s weak prefix, "
             "%s ambiguous", f"{len(mapping):,}", f"{kinds['exact']:,}",
             f"{kinds['prefix_strong']:,}", f"{kinds['prefix_weak']:,}",
             f"{kinds['prefix_ambiguous']:,}")
    log.info("Only exact and strong-prefix matches are safe to state as fact.")
    if kinds["prefix_ambiguous"]:
        log.info("%s companies prefix several legal entities and are left "
                 "undecided; candidates recorded for review.",
                 f"{kinds['prefix_ambiguous']:,}")


def load_summary(cur) -> None:
    """Replace dol_employer_summary from the Spark output. Quarterly."""
    files = glob.glob(f"{SUMMARY_DIR}/**/*.parquet", recursive=True)
    if not files:
        raise SystemExit(f"No parquet under {SUMMARY_DIR}/ - run processing/dol_spark.py first")

    frames = []
    for f in files:
        df = pd.read_parquet(f)
        # fiscal_year is a Hive partition directory, not a column in the file.
        if "fiscal_year" not in df.columns:
            df["fiscal_year"] = f.split("fiscal_year=")[1].split("/")[0]
        frames.append(df)
    summary = pd.concat(frames, ignore_index=True)
    log.info("Read %s employer-year rows from %s files", f"{len(summary):,}", len(files))

    rows = [tuple(
        list(r[c]) if c == "tech_soc_titles" and r[c] is not None else
        (None if pd.isna(r[c]) else r[c]) if c != "tech_soc_titles" else []
        for c in COLUMNS
    ) for _, r in summary.iterrows()]

    cur.execute("TRUNCATE dol_employer_summary")
    execute_values(cur, f"INSERT INTO dol_employer_summary ({','.join(COLUMNS)}) VALUES %s",
                   rows, page_size=500)
    log.info("Loaded %s employer-year rows", f"{len(rows):,}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Load DOL filings and match them to companies")
    ap.add_argument("--rebuild-mapping-only", action="store_true",
                    help="rebuild the company mapping from Postgres; skip the Parquet load")
    args = ap.parse_args()

    conn = connect()
    log.info("Writing to %s", describe())
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        if not args.rebuild_mapping_only:
            load_summary(cur)
        build_mapping(cur)

        # Reported two ways, because they answer different questions. The
        # company count says how complete the mapping is; the posting-weighted
        # share says what a visitor actually encounters, since one matched
        # employer with 500 postings matters more than fifty with one each.
        cur.execute("""
            SELECT count(*) FILTER (WHERE match_type IN ('exact','prefix_strong')),
                   count(*)
            FROM company_employer_key
        """)
        confident, total = cur.fetchone()
        log.info("Confident mapping for %s of %s companies (%.1f%%)",
                 f"{confident:,}", f"{total:,}", 100.0 * confident / max(total, 1))

        cur.execute("""
            SELECT count(*) FILTER (
                       WHERE c.match_type IN ('exact','prefix_strong')),
                   count(*)
            FROM raw_postings r
            LEFT JOIN company_identity ci
                   ON ci.company_name = nullif(trim(r.company_name), '')
            LEFT JOIN company_employer_key c
                   ON c.company_name = coalesce(ci.canonical_name,
                                                nullif(trim(r.company_name), ''))
        """)
        pw_matched, pw_total = cur.fetchone()
        log.info("Postings whose employer has confident sponsorship evidence: "
                 "%s of %s (%.1f%%)", f"{pw_matched:,}", f"{pw_total:,}",
                 100.0 * pw_matched / max(pw_total, 1))

    conn.close()


if __name__ == "__main__":
    main()
