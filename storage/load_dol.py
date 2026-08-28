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

Usage:
    python storage/load_dol.py
"""

from __future__ import annotations

import glob
import logging
import os
import sys

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
"""

COLUMNS = ["employer_key", "fiscal_year", "employer_name", "filings", "certified",
           "certified_pct", "tech_filings", "tech_pct", "distinct_titles",
           "distinct_states", "median_wage", "p25_wage", "p75_wage", "max_wage",
           "tech_soc_titles", "rank_in_year"]


def main() -> None:
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

    conn = connect()
    log.info("Writing to %s", describe())
    with conn, conn.cursor() as cur:
        cur.execute(DDL)

        rows = [tuple(
            list(r[c]) if c == "tech_soc_titles" and r[c] is not None else
            (None if pd.isna(r[c]) else r[c]) if c != "tech_soc_titles" else []
            for c in COLUMNS
        ) for _, r in summary.iterrows()]

        cur.execute("TRUNCATE dol_employer_summary")
        execute_values(cur, f"INSERT INTO dol_employer_summary ({','.join(COLUMNS)}) VALUES %s",
                       rows, page_size=500)
        log.info("Loaded %s employer-year rows", f"{len(rows):,}")

        # Build the canonical key for every company we have postings for.
        cur.execute("SELECT DISTINCT company_name FROM raw_postings WHERE company_name IS NOT NULL")
        companies = [c[0] for c in cur.fetchall()]
        cur.execute("SELECT DISTINCT employer_key FROM dol_employer_summary")
        dol_keys = {k[0] for k in cur.fetchall()}

        # Sorted once so prefix lookups do not rescan the whole set per company.
        sorted_keys = sorted(dol_keys)

        mapping = []
        for company in companies:
            key = normalise_employer(company)
            if not key:
                mapping.append((company, None, None))
                continue
            if key in dol_keys:
                mapping.append((company, key, "exact"))
                continue
            # Prefix fallback: DOL files under legal entity names while job
            # boards use brand names. Require a word boundary and a reasonably
            # specific stem - a short key like "APPLE" would otherwise match
            # "APPLE MOVERS" and put a false sponsorship claim in an email.
            if len(key) >= 8:
                import bisect
                i = bisect.bisect_left(sorted_keys, key)
                hits = []
                while i < len(sorted_keys) and sorted_keys[i].startswith(key):
                    if sorted_keys[i] == key or sorted_keys[i][len(key):len(key) + 1] == " ":
                        hits.append(sorted_keys[i])
                    i += 1
                if hits:
                    # Confidence depends on how distinctive the brand name is.
                    # "CAPITAL ONE" -> "CAPITAL ONE NATIONAL ASSOCIATION" is
                    # safe. A single generic word is not: sampling found
                    # "Lighthouse" -> "LIGHTHOUSE BEHAVIORAL SOLUTIONS" and
                    # "Invictus" -> "INVICTUS ACADEMY OF RICHMOND", which are
                    # different organisations entirely. Rather than guess, the
                    # weak case is labelled so nothing downstream states it as
                    # fact in front of a stranger.
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
        from collections import Counter
        kinds = Counter(m[2] for m in mapping if m[2])
        log.info("Match confidence: %s exact, %s strong prefix, %s weak prefix",
                 f"{kinds['exact']:,}", f"{kinds['prefix_strong']:,}",
                 f"{kinds['prefix_weak']:,}")
        log.info("Only exact and strong-prefix matches are safe to state as fact.")

        cur.execute("""
            SELECT count(*) FILTER (WHERE d.employer_key IS NOT NULL), count(*)
            FROM company_employer_key c
            LEFT JOIN (SELECT DISTINCT employer_key FROM dol_employer_summary) d
                   ON d.employer_key = c.employer_key
        """)
        matched, total = cur.fetchone()
        log.info("Matched %s of %s companies to a sponsoring employer (%.1f%%)",
                 f"{matched:,}", f"{total:,}", 100.0 * matched / total)

    conn.close()


if __name__ == "__main__":
    main()
