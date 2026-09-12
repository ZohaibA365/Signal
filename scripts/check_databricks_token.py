"""
Warn before the Databricks token expires, rather than after.

The DOL job runs quarterly, on the 4th of January, April, July and October. The
token in use expires on 2026-10-17, which falls between two of those dates - so
the first run to notice would be January's, three months of silence after the
token died, on the one workflow whose whole point is that it needs no attention.

A quarterly job cannot watch its own credentials. Something that runs often has
to, which is why this is called from the daily pipeline as well as from the DOL
workflow itself.

Exit status is deliberately 0 even when the token is about to expire: this is an
early warning, and failing a pipeline over a credential that still works would
make the warning worse than useless. The DOL workflow's own credential check is
what fails when the token has actually stopped working.

Usage:
    python scripts/check_databricks_token.py            # warn under 45 days
    python scripts/check_databricks_token.py --days 90
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv

load_dotenv()


def annotate(level: str, message: str) -> None:
    """A GitHub annotation when running in a workflow, a plain line otherwise."""
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::{level}::{message}")
    else:
        print(f"{level}: {message}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Warn before the Databricks token expires")
    ap.add_argument("--days", type=int, default=45,
                    help="warn when the token expires within this many days")
    args = ap.parse_args()

    host = (os.getenv("DATABRICKS_HOST") or "").rstrip("/")
    token = os.getenv("DATABRICKS_TOKEN") or ""
    if not host or not token:
        # Not an error here. The workflow that needs Databricks checks for these
        # itself and fails; this is a watchdog and may simply have nothing to
        # watch, for instance on a fork with no secrets.
        print("DATABRICKS_HOST or DATABRICKS_TOKEN is not set, nothing to check")
        return 0

    try:
        r = requests.get(f"{host}/api/2.0/token/list",
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except requests.RequestException as exc:
        annotate("warning", f"could not reach Databricks to check the token: {exc}")
        return 0

    if r.status_code == 403:
        annotate("warning", "the Databricks token cannot list tokens, so its expiry "
                            "cannot be checked from here")
        return 0
    if r.status_code != 200:
        annotate("warning", f"Databricks token check returned {r.status_code}")
        return 0

    # The API never returns the token string, so a token cannot be matched to the
    # secret in use. The earliest expiry is the honest answer: with one token it is
    # exactly right, and with several it warns early rather than late.
    expiries = []
    for info in r.json().get("token_infos", []):
        ms = info.get("expiry_time", -1)
        if ms and ms > 0:
            expiries.append((datetime.fromtimestamp(ms / 1000, UTC), info.get("comment")))

    if not expiries:
        print("no Databricks token has an expiry date")
        return 0

    when, comment = min(expiries)
    days = (when - datetime.now(UTC)).days
    label = f"Databricks token{f' ({comment})' if comment else ''}"

    if days < 0:
        annotate("error", f"{label} EXPIRED on {when:%Y-%m-%d}. The DOL refresh cannot "
                          f"run until a new one is in DATABRICKS_TOKEN.")
    elif days <= args.days:
        annotate("warning",
                 f"{label} expires on {when:%Y-%m-%d}, in {days} days. The DOL refresh "
                 f"runs quarterly, so generate a new token and update the "
                 f"DATABRICKS_TOKEN secret before then.")
    else:
        print(f"{label} expires {when:%Y-%m-%d}, in {days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
