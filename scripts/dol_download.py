"""
Fetch the DOL H-1B disclosure files the Spark job needs.

They arrived by hand and nothing could reproduce them. ingestion/dol_ingest.py
reads data/dol/*.xlsx and exits with "download them first", which was the only
instruction anywhere in the repo - no URL, no script, no note of which quarters.
That makes the input to the only Spark job in the project a manual step nobody
else can repeat, including a future version of its author.

It also hid a second problem. The six files appeared present at 576 MB total and
had **zero disk blocks**: iCloud had evicted the contents and left placeholders
with the right sizes. Every length check based on st_size passes against those,
and reading one fails. So this checks blocks allocated rather than apparent
size, and treats a placeholder as absent.

URLs verified by HEAD before this was written, and the Content-Length of each
matched the local placeholder exactly, which is how the pattern was confirmed
rather than guessed.

Fetched with requests rather than urllib. urllib verifies TLS against the system
trust store, which this machine's Python cannot read - the same failure
dbt_project.yml records when it disabled telemetry to stop a hang - so every
HEAD returned CERTIFICATE_VERIFY_FAILED. requests ships certifi and works, and
it is what the rest of the ingestion layer already uses.

Usage:
    python scripts/dol_download.py                       # the default quarters
    python scripts/dol_download.py --quarters 2026Q1 2025Q4
    python scripts/dol_download.py --check               # report, download nothing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("dol_download")

BASE = "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs"
DEST = "data/dol"

# What the warehouse currently holds, so a rebuild reproduces it. Three fiscal
# years, six quarters, about 576 MB and 800,569 filings.
DEFAULT_QUARTERS = ["2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"]

CHUNK = 1 << 20


def filename(quarter: str) -> str:
    year, q = quarter[:4], quarter[-1]
    return f"LCA_Disclosure_Data_FY{year}_Q{q}.xlsx"


def real_bytes(path: str) -> int:
    """
    Bytes actually on this disk, which is not st_size.

    A cloud-evicted file reports its full size and allocates nothing. st_blocks
    is in 512-byte units by POSIX convention.
    """
    if not os.path.exists(path):
        return 0
    st = os.stat(path)
    return st.st_blocks * 512


def remote_size(url: str) -> int | None:
    try:
        resp = requests.head(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        value = resp.headers.get("Content-Length")
        return int(value) if value else None
    except requests.RequestException as exc:
        log.warning("  HEAD failed for %s: %s", url, exc)
        return None


def status(quarter: str) -> tuple[str, str, int, int | None]:
    """(verdict, path, bytes present, bytes expected) for one quarter."""
    name = filename(quarter)
    path = os.path.join(DEST, name)
    expected = remote_size(f"{BASE}/{name}")
    present = real_bytes(path)
    apparent = os.path.getsize(path) if os.path.exists(path) else 0

    if expected and present >= expected:
        return "complete", path, present, expected
    if apparent and not present:
        return "placeholder", path, present, expected
    if present:
        return "partial", path, present, expected
    return "missing", path, present, expected


def download(url: str, path: str, expected: int | None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    got = 0
    with requests.get(url, timeout=120, stream=True) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as out:
            for chunk in resp.iter_content(CHUNK):
                out.write(chunk)
                got += len(chunk)
                if got % (32 << 20) < CHUNK:
                    log.info("    %s MB", f"{got // (1 << 20):,}")

    if expected and got != expected:
        # Written to .part and not moved, so a truncated download can never be
        # mistaken for a complete file by the next run.
        raise SystemExit(f"{path}: got {got:,} bytes, expected {expected:,}")
    os.replace(tmp, path)
    log.info("    %s MB -> %s", f"{got // (1 << 20):,}", path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Download DOL H-1B disclosure files")
    ap.add_argument("--quarters", nargs="+", default=DEFAULT_QUARTERS,
                    metavar="YYYYQn", help="e.g. 2026Q1 2025Q4")
    ap.add_argument("--check", action="store_true", help="report only, download nothing")
    args = ap.parse_args()

    todo = []
    for quarter in args.quarters:
        verdict, path, present, expected = status(quarter)
        note = {
            "complete": "already here",
            "placeholder": "cloud placeholder, 0 bytes on disk",
            "partial": f"incomplete, {present:,} of {expected or 0:,}",
            "missing": "not downloaded",
        }[verdict]
        log.info("  %-7s %-44s %s", quarter, filename(quarter), note)
        if verdict != "complete":
            todo.append((quarter, path, expected))

    if args.check:
        log.info("%s of %s quarter(s) need downloading", len(todo), len(args.quarters))
        return
    if not todo:
        log.info("Nothing to do - all %s quarter(s) present.", len(args.quarters))
        return

    total = sum(e or 0 for _q, _p, e in todo)
    log.info("Downloading %s file(s), about %s MB", len(todo), f"{total // (1 << 20):,}")
    for quarter, path, expected in todo:
        log.info("  %s", filename(quarter))
        download(f"{BASE}/{filename(quarter)}", path, expected)
    log.info("Done. Next: python ingestion/dol_ingest.py")


if __name__ == "__main__":
    sys.exit(main())
