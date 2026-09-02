"""
LLM enrichment: eligibility, sponsorship signal, and fit scoring.

The master spec called for two Claude calls per posting (visa_detector and
fit_scorer). This does both in a single call, because the two judgements read
the same text and share the same profile context - splitting them doubles the
cost and latency for no gain in quality.

Cost control, in order of impact:
  1. One call per posting instead of two.
  2. Incremental. A posting is re-scored only when its description changes,
     tracked by description_hash. A daily run touches only new postings.
  3. Prompt caching. The profile and instructions are a stable prefix, so
     after the first call they are billed at roughly a tenth of the rate.

Usage:
    python ai_layer/enrich.py --limit 5          # try a handful first
    python ai_layer/enrich.py --seniority intern entry
    python ai_layer/enrich.py --force            # re-score everything
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from profile import ACTIVE as ACTIVE_PROFILE
from profile import as_prompt_context

import anthropic
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage"))
from db import connect  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("enrich")

MODEL = "claude-opus-5"

# Price per token by model, so a run reports what it actually cost rather than
# what Opus would have cost. Opus reasons better on the genuinely hard calls -
# visa eligibility on a posting that never states it - which is why the roles
# this profile would actually apply to are still scored with it. The rest of
# the board is structured classification, where Haiku is ~5x cheaper for
# little loss: scoring every linkable relevant role is ~$25 on Haiku against
# ~$123 on Opus.
# Which models accept the 4.6+ reasoning controls (adaptive thinking and the
# effort parameter). Haiku 4.5 predates both.
THINKS = {"claude-opus-5": True, "claude-haiku-4-5-20251001": False}

RATES = {
    # model: (input, cache_read, output) per token
    "claude-opus-5":            (5/1e6, 0.5/1e6, 25/1e6),
    "claude-haiku-4-5-20251001": (1/1e6, 0.1/1e6,  5/1e6),
}


class Assessment(BaseModel):
    """The structured verdict Claude returns for one posting."""

    eligibility: str = Field(
        description="'blocked' if the posting requires US citizenship, an active "
                    "security clearance, or existing work authorisation with no "
                    "sponsorship; 'eligible' if a sponsored international student "
                    "could hold it; 'unclear' if the text does not say."
    )
    eligibility_reason: str = Field(description="One sentence justifying the eligibility call.")
    sponsorship_signal: str = Field(
        description="'sponsors', 'no_sponsorship', or 'unclear' - what the posting "
                    "says about supporting work authorisation."
    )
    visa_reasoning: str = Field(description="One sentence on the sponsorship signal.")
    fit_score: int = Field(ge=0, le=100, description="How worth applying to this is, 0-100.")
    fit_reasoning: str = Field(description="One sentence justifying the score.")
    tech_stack: list[str] = Field(description="Tools and technologies named in the posting.")
    concerns: list[str] = Field(description="Short flags worth knowing before applying. May be empty.")


SYSTEM_PROMPT = f"""You assess US job postings for one specific candidate.

{as_prompt_context()}

HOW TO SCORE

Eligibility comes first. If a posting requires US citizenship or an active
security clearance, it is 'blocked' no matter how well it otherwise matches -
this candidate cannot hold it. Defence and government contractors frequently
carry these requirements.

The description text you receive is TRUNCATED to roughly 500 characters by the
data source, so the legal and work-authorisation boilerplate that normally sits
at the end of a posting is usually missing. Two fields therefore have different
evidence rules:

  - sponsorship_signal reports ONLY what the posting text actually states. If
    the text is silent on work authorisation, this is 'unclear', however
    confident you are about the employer. Do not invent requirements.
  - eligibility is your overall judgement and MAY draw on well-established
    knowledge about the employer - for example, that US defence contractors
    almost always require citizenship or clearance. When you rely on that
    rather than the text, say so plainly in eligibility_reason.

fit_score should reflect how worth this candidate's limited application time the
posting is. Weigh:
  - Term match. They need a Winter 2027 (Jan-Apr) internship. A Summer-only role
    is a poor fit even if the work is ideal. An internship posted for 2027 is
    better than one for 2026, which has likely passed.
  - Seniority. They are a student. Senior, staff, and lead roles score near zero.
  - Skill overlap with Python, SQL, dbt, AWS, Docker, and pipeline work.
  - Eligibility. A blocked posting should score below 10 regardless of fit.

Be direct and calibrated. Most postings are mediocre for this candidate and
should score in the 20-50 range. Reserve scores above 80 for genuinely strong
matches: a data-focused internship, open to sponsorship, in the right term."""


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


# Titles worth paying to score for this profile. Seniority alone is not
# enough: "entry" matches Consumer Sales Associate and Legal Content
# Development Associate as readily as it matches Data Engineer Intern, and
# each one costs a full-description API call to learn it was never relevant.
# "develop" was matching Business Development Associate, and a false positive
# here is not free - it is a full-description API call spent to learn the role
# was never in scope. Stems are anchored to the words that actually appear in
# engineering titles.
RELEVANT_TITLE = (
    r"data|analytic|analyst|engineer|developer|software|machine learning|"
    r"data scien|platform|infrastructur|backend|back-end|full.?stack|python|"
    r"\msql\M|cloud|devops|\msre\M|quantitative|research engineer")


def fetch_candidates(cur, seniority, limit, force, table: str, profile: str,
                     min_salary: int | None = None, linkable: bool = False,
                     relevant: bool = False):
    """
    Roles that still need scoring, for one profile.

    Reads a marts table rather than staging: the model has already collapsed a
    role posted across many cities into one row, so we do not pay for the same
    judgement once per city.

    The join carries `profile` because a score only means something relative to
    the profile it was made against - the same posting is a 5 for a student and
    an 85 for an experienced hire.
    """
    params: dict = {"profile": profile}
    sql = """
        SELECT r.source, r.job_id, r.company_name, r.job_title, r.location_raw,
               r.location_state, r.posted_date::date, r.seniority, r.description_raw,
               r.salary_min, r.salary_is_predicted
        FROM {table} r
        LEFT JOIN job_enrichment e
               ON e.source = r.source AND e.job_id = r.job_id
              AND e.profile = %(profile)s
        WHERE r.description_raw IS NOT NULL
    """.replace("{table}", table)

    if not force:
        sql += (" AND (e.job_id IS NULL OR e.description_hash <> "
                "substring(encode(sha256(convert_to(r.description_raw,'UTF8')),'hex') for 16))")
    if relevant:
        sql += " AND r.job_title ~* %(relevant)s"
        params["relevant"] = RELEVANT_TITLE
    if linkable:
        # Only postings the site can actually publish. Scoring is the most
        # expensive step in the pipeline and an aggregator-sourced posting is
        # never shown - its link is country-gated and cannot be resolved - so
        # paying to score one buys nothing.
        sql += " AND r.link_tier = 'direct'"
    if seniority:
        sql += " AND r.seniority = ANY(%(seniority)s)"
        params["seniority"] = seniority
    if min_salary:
        # Keep postings with no salary at all: absence of a figure is not
        # evidence the role pays badly, and ~99% of present figures are the
        # source's estimates rather than employer-published numbers.
        sql += " AND (r.salary_min IS NULL OR r.salary_min >= %(min_salary)s)"
        params["min_salary"] = min_salary

    sql += " ORDER BY r.days_since_posted ASC"
    if limit:
        sql += " LIMIT %(limit)s"
        params["limit"] = limit

    cur.execute(sql, params)
    return cur.fetchall()


def assess(client: anthropic.Anthropic, row, model: str = MODEL):
    (_src, _jid, company, title, location, state, posted, seniority,
     description, salary_min, salary_predicted) = row

    salary = "not stated"
    if salary_min:
        salary = f"~${int(salary_min):,}" + (" (site estimate, not from the posting)"
                                             if salary_predicted else " (stated)")

    posting = f"""JOB POSTING

Title: {title}
Company: {company}
Location: {location}{f', {state}' if state else ''}
Posted: {posted}
Salary: {salary}
Title-derived seniority: {seniority}

Description (truncated by the source at ~500 characters):
{description}"""

    response = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            # Stable across every posting, so it caches after the first call.
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": posting}],
        output_format=Assessment,
        # Adaptive thinking is a 4.6+ feature and Haiku 4.5 rejects it with a
        # 400 rather than ignoring it, so it is passed only where supported.
        # Haiku is doing structured classification here, which is the case
        # that needs it least.
        # Adaptive thinking and the effort parameter are both 4.6+ features
        # that Haiku 4.5 rejects with a 400 rather than ignoring, so they are
        # sent only where supported. Haiku is doing structured classification
        # here, which is the case that needs them least.
        **({"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
           if THINKS.get(model, True) else {}),
    )
    return response.parsed_output, response.usage


UPSERT = """
INSERT INTO job_enrichment (
    source, job_id, eligibility, eligibility_reason, sponsorship_signal,
    visa_reasoning, fit_score, fit_reasoning, tech_stack, concerns,
    model, description_hash, profile, enriched_at
) VALUES %s
ON CONFLICT (source, job_id, profile) DO UPDATE SET
    eligibility        = EXCLUDED.eligibility,
    eligibility_reason = EXCLUDED.eligibility_reason,
    sponsorship_signal = EXCLUDED.sponsorship_signal,
    visa_reasoning     = EXCLUDED.visa_reasoning,
    fit_score          = EXCLUDED.fit_score,
    fit_reasoning      = EXCLUDED.fit_reasoning,
    tech_stack         = EXCLUDED.tech_stack,
    concerns           = EXCLUDED.concerns,
    model              = EXCLUDED.model,
    description_hash   = EXCLUDED.description_hash,
    enriched_at        = NOW()
"""


def flush(results: list) -> int:
    """
    Write a batch on a connection opened just for it.

    Scoring holds no connection between writes. A run of a few hundred roles
    takes long enough that Neon - being serverless - closes an idle one
    underneath it, and a 176-role run died on exactly that after paying for 80
    assessments. Those 80 survived only because writes are batched; the
    connection now lives no longer than a single batch.
    """
    if not results:
        return 0
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, UPSERT, [r[:13] + ("NOW()",) for r in results],
                           page_size=100)
        return len(results)
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Score postings with Claude")
    ap.add_argument("--limit", type=int, help="max postings to score this run")
    ap.add_argument("--seniority", nargs="+", help="e.g. intern entry")
    ap.add_argument("--force", action="store_true", help="re-score already-scored postings")
    ap.add_argument("--linkable", action="store_true",
                    help="only postings with a working employer link (what the site shows)")
    ap.add_argument("--relevant", action="store_true",
                    help="only titles plausibly in scope for this profile")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent scoring requests")
    ap.add_argument("--model", default=MODEL, choices=sorted(RATES),
                    help="scoring model; Haiku is ~5x cheaper for bulk classification")
    ap.add_argument("--table", default="ranked_opportunities",
                    help="marts table or view to score from")
    ap.add_argument("--min-salary", type=int,
                    help="skip roles whose stated/estimated salary is below this")
    args = ap.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")

    # The default 10-minute timeout with retries can wedge a run for half an
    # hour on a single stalled request. These are short classification calls -
    # if one has not returned in 90s it is not going to.
    client = anthropic.Anthropic(timeout=90.0, max_retries=3)
    # The shared connection, not the POSTGRES_* variables. Reading those
    # directly targets the local container while dbt and the site read the
    # hosted warehouse - and in CI they are not set at all, so this step
    # dialled a localhost that does not exist there and failed every run.
    conn = connect()
    try:
        with conn.cursor() as cur:
            rows = fetch_candidates(cur, args.seniority, args.limit, args.force,
                                    args.table, ACTIVE_PROFILE, args.min_salary,
                                    args.linkable, args.relevant)
    finally:
        conn.close()

    log.info("%s roles to score with %s [profile=%s, table=%s]",
             len(rows), args.model, ACTIVE_PROFILE, args.table)
    if not rows:
        log.info("Nothing to do - everything current is already scored.")
        return

    results, written = [], 0
    consecutive_failures = 0
    FLUSH_EVERY = 40
    RATE_IN, RATE_CACHE_READ, RATE_OUT = RATES.get(args.model, RATES[MODEL])
    cost = 0.0
    lock = threading.Lock()
    stop = threading.Event()

    def score_one(idx_row):
        """One assessment. Runs on a worker; all shared state is under lock."""
        nonlocal consecutive_failures, cost, written, results
        i, row = idx_row
        if stop.is_set():
            return
        try:
            a, usage = assess(client, row, args.model)
        except anthropic.APIStatusError as exc:
            # Log the actual message, not just the status. A bare "API error
            # 400" is useless: billing exhaustion, a malformed request and an
            # unsupported parameter all look identical.
            detail = getattr(exc, "message", None) or str(exc)
            with lock:
                consecutive_failures += 1
                log.error("  [%s/%s] %s - HTTP %s: %s",
                          i, len(rows), (row[3] or "")[:40], exc.status_code,
                          detail[:200])
                # 4xx other than rate limiting will not fix itself on the next
                # row. Exhausted credit produced 352 identical failures over
                # ten minutes before this guard existed.
                if exc.status_code in (400, 401, 403) and consecutive_failures >= 3:
                    log.error("Aborting: %s consecutive HTTP %s failures. This is "
                              "an account or request problem, not bad data. Rows "
                              "scored so far are already saved.",
                              consecutive_failures, exc.status_code)
                    stop.set()
            return
        except Exception as exc:
            with lock:
                log.error("  [%s/%s] %s - %s", i, len(rows),
                          (row[3] or "")[:40], type(exc).__name__)
            return

        with lock:
            consecutive_failures = 0
            results.append((
                row[0], row[1], a.eligibility, a.eligibility_reason,
                a.sponsorship_signal, a.visa_reasoning, a.fit_score,
                a.fit_reasoning, a.tech_stack, a.concerns,
                args.model, _hash(row[8]), ACTIVE_PROFILE, None,
            ))
            cost += (usage.input_tokens * RATE_IN
                     + (usage.cache_read_input_tokens or 0) * RATE_CACHE_READ
                     + (usage.cache_creation_input_tokens or 0) * RATE_IN * 1.25
                     + usage.output_tokens * RATE_OUT)
            log.info("  [%s/%s] %-3s %-9s %s @ %s", i, len(rows), a.fit_score,
                     a.eligibility, (row[3] or "")[:44], (row[2] or "")[:22])
            # Flush periodically rather than once at the end. A long run that
            # dies at role 150 would otherwise discard 150 paid-for API calls.
            if len(results) >= FLUSH_EVERY:
                batch, results = results, []
                written += flush(batch)

    # Scored concurrently. Serially this ran at roughly six roles a minute -
    # seventeen hours for the board, which no run survives. The work is
    # entirely network-bound, so workers cost nothing but politeness.
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(score_one, enumerate(rows, 1)))

    written += flush(results)

    log.info("Scored %s postings. Cost $%.3f ($%.4f each).",
             written, cost, cost / max(written, 1))


if __name__ == "__main__":
    main()
