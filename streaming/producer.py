"""
Publish newly seen job postings to Kafka.

Why streaming exists in a batch project: the daily pipeline is the right shape
for building a market index, and the wrong shape for acting on a posting. Good
internships close in days, and the ranked feed is only as fresh as the last
07:00 run - a role posted at 09:00 is invisible for 22 hours. This path exists
to close that gap, and it is the only reason to add a broker here. It is not
"batch, but with Kafka in front".

Publishes postings first seen since the last high-water mark, keyed by
(source, job_id) so a partition holds all events for a posting in order and a
replay cannot reorder them.

Usage:
    python streaming/producer.py                 # publish new postings once
    python streaming/producer.py --watch 60      # poll every 60s
    python streaming/producer.py --replay 200    # re-publish recent, for testing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from kafka import KafkaProducer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
from db import connect  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("producer")

BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
TOPIC = "signal.postings.new"
STATE_TABLE = "streaming_watermark"

DDL = f"""
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
    stream_name  TEXT PRIMARY KEY,
    last_seen_at TIMESTAMPTZ NOT NULL
);
"""

SELECT_NEW = """
SELECT r.source, r.job_id, r.company_name, r.job_title, r.location as location_raw,
       r.location_state, r.country, r.posted_date, r.redirect_url,
       r.description_raw, r.first_seen
FROM raw_postings r
WHERE r.first_seen > %(since)s
ORDER BY r.first_seen
LIMIT %(limit)s
"""


def make_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v, default=str).encode(),
        key_serializer=lambda k: k.encode(),
        # Wait for the broker to acknowledge. A dropped posting is a missed
        # opportunity, and throughput here is trivially low, so durability wins.
        acks="all",
        retries=3,
        linger_ms=50,
    )


def publish_once(producer: KafkaProducer, conn, limit: int, replay: int | None) -> int:
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        if replay:
            cur.execute("SELECT now() - interval '30 days'")
            since = cur.fetchone()[0]
            limit = replay
        else:
            cur.execute(f"SELECT last_seen_at FROM {STATE_TABLE} WHERE stream_name = %s",
                        (TOPIC,))
            row = cur.fetchone()
            since = row[0] if row else datetime(2000, 1, 1, tzinfo=timezone.utc)

        cur.execute(SELECT_NEW, {"since": since, "limit": limit})
        cols = [c.name for c in cur.description]
        rows = cur.fetchall()

        for row in rows:
            posting = dict(zip(cols, row))
            producer.send(TOPIC, key=f"{posting['source']}:{posting['job_id']}",
                          value=posting)
        producer.flush()

        if rows and not replay:
            newest = max(dict(zip(cols, r))["first_seen"] for r in rows)
            cur.execute(f"""
                INSERT INTO {STATE_TABLE} (stream_name, last_seen_at) VALUES (%s, %s)
                ON CONFLICT (stream_name) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at
            """, (TOPIC, newest))
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish new postings to Kafka")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="poll continuously")
    ap.add_argument("--limit", type=int, default=500, help="max postings per pass")
    ap.add_argument("--replay", type=int, metavar="N",
                    help="re-publish N recent postings regardless of watermark")
    args = ap.parse_args()

    producer = make_producer()
    conn = connect()
    log.info("Producing to %s on %s", TOPIC, BROKER)

    try:
        while True:
            n = publish_once(producer, conn, args.limit, args.replay)
            log.info("published %s posting(s)", n)
            if not args.watch:
                break
            time.sleep(args.watch)
    finally:
        producer.close()
        conn.close()


if __name__ == "__main__":
    main()
