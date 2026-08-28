"""
Consume new postings and alert on the ones worth acting on immediately.

The point of this path is latency. A posting that appears at 09:00 is
invisible to the batch feed until the next 07:00 run, and good internships
close in days - so the value here is measured in hours saved, not throughput.

Deliberately cheap before it is smart. Every posting is screened with free
checks first (title relevance, seniority, verified sponsorship, technology
overlap) and only survivors reach the LLM. A consumer that called an API for
every message would spend money proportional to the firehose rather than to
the number of genuinely interesting roles - and the firehose is mostly noise.

Alerts are written to a table and printed. Wiring them to email or a webhook
is a delivery detail; the judgement is the part that matters.

Usage:
    python streaming/alerter.py                  # consume until idle
    python streaming/alerter.py --follow         # stay running
    python streaming/alerter.py --min-score 75
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv
from kafka import KafkaConsumer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ai_layer"))
from db import connect  # noqa: E402
from taxonomy import match_technologies  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("alerter")

BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC = "signal.postings.new"

DDL = """
CREATE TABLE IF NOT EXISTS posting_alerts (
    source        TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    company_name  TEXT,
    job_title     TEXT,
    reason        TEXT,
    screen_score  INTEGER,
    sponsorship   TEXT,
    alerted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, job_id)
);
"""

# Free screening. These decide whether a posting is worth any further thought.
WANTED_TITLE = ("data engineer", "analytics engineer", "data platform",
                "software engineer", "data science", "machine learning")
REJECT_TITLE = ("senior", "staff", "principal", "director", "manager", "lead",
                "vp ", "head of", "architect")
WANTED_TECH = {"python", "sql", "dbt", "spark", "airflow", "snowflake",
               "databricks", "kafka", "postgresql", "aws"}


def screen(posting: dict, sponsorship: dict) -> tuple[int, list[str]]:
    """Score a posting without spending anything. Returns (score, reasons)."""
    title = (posting.get("job_title") or "").lower()
    desc = posting.get("description_raw") or ""
    score, reasons = 0, []

    if any(w in title for w in WANTED_TITLE):
        score += 30
        reasons.append("title matches target roles")
    if any(w in title for w in REJECT_TITLE):
        score -= 40
        reasons.append("senior-level title")
    if any(w in title for w in ("intern", "co-op", "coop", "new grad", "entry")):
        score += 30
        reasons.append("student-level role")

    techs = set(match_technologies(f"{title} {desc}"))
    overlap = techs & WANTED_TECH
    if overlap:
        score += min(len(overlap) * 8, 24)
        reasons.append(f"uses {', '.join(sorted(overlap)[:3])}")

    status = sponsorship.get(posting.get("company_name"))
    if status == "frequent_sponsor":
        score += 20
        reasons.append("employer sponsors frequently")
    elif status == "has_sponsored":
        score += 10
        reasons.append("employer has sponsored before")

    if posting.get("country") not in (None, "us", "ca"):
        score -= 30

    return score, reasons


def load_sponsorship(cur) -> dict:
    """Verified filing history, so the screen can prefer real sponsors."""
    try:
        cur.execute("""SELECT company_name, sponsorship_status
                       FROM int_company_sponsorship WHERE is_confident_match""")
        return dict(cur.fetchall())
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Alert on high-fit postings in near real time")
    ap.add_argument("--min-score", type=int, default=60)
    ap.add_argument("--follow", action="store_true", help="keep consuming")
    ap.add_argument("--from-start", action="store_true", help="read the topic from the beginning")
    args = ap.parse_args()

    conn = connect(autocommit=True)
    cur = conn.cursor()
    cur.execute(DDL)
    sponsorship = load_sponsorship(cur)
    log.info("Loaded verified sponsorship for %s companies", f"{len(sponsorship):,}")

    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=BROKER,
        value_deserializer=lambda v: json.loads(v.decode()),
        auto_offset_reset="earliest" if args.from_start else "latest",
        enable_auto_commit=True,
        group_id="signal-alerter",
        consumer_timeout_ms=None if args.follow else 8000,
    )
    log.info("Consuming %s (min score %s)%s", TOPIC, args.min_score,
             " [following]" if args.follow else "")

    seen = alerted = 0
    for message in consumer:
        posting = message.value
        seen += 1
        score, reasons = screen(posting, sponsorship)
        if score < args.min_score:
            continue

        alerted += 1
        reason = "; ".join(reasons)
        cur.execute("""
            INSERT INTO posting_alerts
                (source, job_id, company_name, job_title, reason, screen_score, sponsorship)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (source, job_id) DO NOTHING
        """, (posting["source"], posting["job_id"], posting.get("company_name"),
              posting.get("job_title"), reason, score,
              sponsorship.get(posting.get("company_name"))))

        log.info("ALERT  %3d  %-34s %-26s  %s", score,
                 (posting.get("job_title") or "")[:34],
                 (posting.get("company_name") or "")[:26], reason)

    log.info("Screened %s posting(s), alerted on %s.", f"{seen:,}", alerted)
    conn.close()


if __name__ == "__main__":
    main()
