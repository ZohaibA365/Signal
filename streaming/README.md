# Streaming

Near-real-time alerting on newly seen postings.

## Why this exists

The batch pipeline is the right shape for building a market index and the
wrong shape for acting on a posting. A role that appears at 09:00 is invisible
to the ranked feed until the next 07:00 run — 22 hours later — and good
internships close in days. The value here is measured in hours saved, not
throughput. That is the only honest reason to add a broker to this project;
it is not "batch, but with Kafka in front".

## Design

```
raw_postings ──► producer ──► topic: signal.postings.new ──► alerter ──► posting_alerts
   (first_seen        (keyed by                              (free screen,
    watermark)     source:job_id)                          then alert)
```

- **Keyed by `source:job_id`** so all events for a posting land in one
  partition and cannot be reordered on replay.
- **`acks="all"`** — a dropped posting is a missed opportunity, and the volume
  here is trivially low, so durability beats throughput.
- **A watermark table**, not offsets, decides what is new. Consumer offsets
  track what was *read*; `first_seen` tracks what *exists*, which is what
  matters when the producer restarts.
- **The consumer screens for free before spending anything.** Title relevance,
  seniority, technology overlap and verified sponsorship are all local checks.
  Calling an LLM per message would cost in proportion to the firehose rather
  than to the number of interesting roles, and the firehose is mostly noise.

## Running

```bash
docker compose up -d kafka

python streaming/producer.py --replay 400     # publish a batch for testing
python streaming/alerter.py --from-start      # screen and alert

python streaming/producer.py --watch 60       # production: poll every minute
python streaming/alerter.py --follow          # production: stay consuming
```

## Note on duplicates

The alert log prints one line per message, but `posting_alerts` is keyed on
`(source, job_id)` so the table deduplicates. A batch producing 26 log lines
stored 26 rows across 14 distinct company+title pairs — the repeats are the
same role posted in different cities, which the marts layer collapses and the
raw stream deliberately does not.
