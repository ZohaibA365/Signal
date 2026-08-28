"""
One command: company names in, opening lines out.

Chains the whole outreach path so a target list needs no manual steps:

    ingest each company's career board  ->  load to the warehouse
    ->  extract technologies  ->  compute insights  ->  print opening lines

Companies already ingested today are skipped, so re-running is cheap.

Usage:
    python outreach/run.py --companies-file targets.txt
    python outreach/run.py --companies Stripe Ramp --skip-ingest
"""

from __future__ import annotations

import argparse
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")


def run(label: str, argv: list[str]) -> None:
    print(f"\n>>> {label}", flush=True)
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"step failed: {label}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Target list -> outreach opening lines")
    ap.add_argument("--companies", nargs="+")
    ap.add_argument("--companies-file")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="use what is already stored; no network calls")
    ap.add_argument("--json", help="also write full insight records here")
    args = ap.parse_args()

    target_args: list[str] = []
    if args.companies:
        target_args += ["--companies", *args.companies]
    if args.companies_file:
        target_args += ["--companies-file", args.companies_file]
    if not target_args:
        raise SystemExit("Give --companies or --companies-file")

    if not args.skip_ingest:
        run("Pulling career boards (Greenhouse / Lever / Ashby)",
            [PY, "ingestion/company_boards.py", *target_args])
        run("Loading into the warehouse",
            [PY, "storage/load_to_warehouse.py", "--boards"])
        run("Extracting technologies (dictionary, no model calls)",
            [PY, "ai_layer/extract_tech.py"])

    insight_args = [PY, "outreach/insights.py", *target_args]
    if args.json:
        insight_args += ["--json", args.json]
    run("Computing insights", insight_args)


if __name__ == "__main__":
    main()
