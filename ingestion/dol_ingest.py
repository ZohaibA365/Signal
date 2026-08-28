"""
DOL H-1B (LCA) disclosure data: XLSX -> Parquet.

Why this dataset matters to Signal: sponsorship has been the weakest signal in
the product. Job descriptions almost never state it - only ~2% of postings
mention citizenship or clearance, and full-text board postings only moved that
to ~4% - so every eligibility verdict has been an inference. These files are
the Department of Labor's record of who actually filed to sponsor a foreign
worker, with the employer, the job title, the worksite and the real offered
wage. It turns an inference into a fact, and it replaces the aggregator's
model-estimated salaries with figures an employer legally attested to.

Why the conversion step exists: Spark cannot read XLSX. The files are also 97
columns wide and mostly irrelevant, so this selects the columns that matter and
writes columnar Parquet partitioned by fiscal quarter - which is what makes the
Spark aggregation afterwards cheap.

Usage:
    python ingestion/dol_ingest.py                  # convert every downloaded file
    python ingestion/dol_ingest.py --limit-rows 5000 --dry-run
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import re
import time

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("dol_ingest")

SRC_DIR = "data/dol"
OUT_DIR = "data/dol_parquet"

# 15 of 97 columns. The rest are attorney contact details, agent addresses and
# form metadata that no downstream question needs.
COLUMNS = [
    "CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE",
    "VISA_CLASS", "JOB_TITLE", "SOC_CODE", "SOC_TITLE",
    "FULL_TIME_POSITION", "BEGIN_DATE", "END_DATE",
    "EMPLOYER_NAME", "WORKSITE_STATE",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY",
]

FILE_RE = re.compile(r"FY(\d{4})_Q(\d)", re.IGNORECASE)

# Legal suffixes carry no information and stop the same employer matching
# itself across filings ("STRIPE, INC." vs "Stripe Inc" vs "STRIPE INC.").
SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|"
    r"plc|lp|llp|pllc|pc|na|usa|us)\b\.?", re.IGNORECASE)


def normalise_employer(name: str | None) -> str | None:
    """Canonical employer key. Matching across sources depends entirely on this."""
    if not name or not isinstance(name, str):
        return None
    s = name.upper().strip()
    s = re.sub(r"[.,]", " ", s)
    s = SUFFIXES.sub(" ", s)
    s = re.sub(r"[^A-Z0-9& ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def annualise(row) -> float | None:
    """
    Normalise the offered wage to an annual figure.

    The wage is meaningless without its unit: the same column holds hourly,
    weekly, monthly and yearly values, so comparing them raw would rank a
    $95/hour contractor below a $60,000 salary.
    """
    wage, unit = row["WAGE_RATE_OF_PAY_FROM"], row["WAGE_UNIT_OF_PAY"]
    if pd.isna(wage) or wage in (0, "0"):
        return None
    try:
        w = float(wage)
    except (TypeError, ValueError):
        return None
    factor = {"Year": 1, "Hour": 2080, "Week": 52, "Bi-Weekly": 26, "Month": 12}
    return round(w * factor.get(str(unit).strip(), 0)) or None


def convert(path: str, limit_rows: int | None, dry_run: bool) -> int:
    name = os.path.basename(path)
    m = FILE_RE.search(name)
    fiscal_year, quarter = (m.group(1), f"Q{m.group(2)}") if m else ("unknown", "unknown")

    t0 = time.time()
    df = pd.read_excel(path, engine="calamine", usecols=COLUMNS,
                       nrows=limit_rows) if limit_rows else \
         pd.read_excel(path, engine="calamine", usecols=COLUMNS)
    read_s = time.time() - t0

    df["employer_key"] = df["EMPLOYER_NAME"].map(normalise_employer)
    df["annual_wage"] = df.apply(annualise, axis=1)
    df["fiscal_year"] = fiscal_year
    df["fiscal_quarter"] = quarter
    df.columns = [c.lower() for c in df.columns]

    log.info("  %-38s %s rows, %d cols, read in %.0fs",
             name, f"{len(df):,}", df.shape[1], read_s)

    if dry_run:
        return len(df)

    out = os.path.join(OUT_DIR, f"fiscal_year={fiscal_year}", f"fiscal_quarter={quarter}")
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, "data.parquet")
    df.drop(columns=["fiscal_year", "fiscal_quarter"]).to_parquet(
        target, compression="snappy", index=False)
    log.info("      -> %s  (%.1f MB parquet from %.1f MB xlsx)",
             target, os.path.getsize(target) / 1e6, os.path.getsize(path) / 1e6)
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert DOL LCA XLSX to Parquet")
    ap.add_argument("--limit-rows", type=int, help="read only N rows per file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--files", nargs="+", help="specific xlsx paths")
    args = ap.parse_args()

    files = args.files or sorted(glob.glob(f"{SRC_DIR}/*.xlsx"))
    if not files:
        raise SystemExit(f"No XLSX files in {SRC_DIR}/ - download them first")

    log.info("Converting %s file(s)", len(files))
    total = sum(convert(f, args.limit_rows, args.dry_run) for f in files)
    log.info("Done. %s filings.", f"{total:,}")


if __name__ == "__main__":
    main()
