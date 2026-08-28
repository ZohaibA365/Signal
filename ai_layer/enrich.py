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
from profile import ACTIVE as ACTIVE_PROFILE
from profile import as_prompt_context

import anthropic
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("enrich")

MODEL = "claude-opus-5"


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


def fetch_candidates(cur, seniority, limit, force, table: str, profile: str,
                     min_salary: int | None = None):
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


def assess(client: anthropic.Anthropic, row):
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
        model=MODEL,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            # Stable across every posting, so it caches after the first call.
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": posting}],
        output_format=Assessment,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Score postings with Claude")
    ap.add_argument("--limit", type=int, help="max postings to score this run")
    ap.add_argument("--seniority", nargs="+", help="e.g. intern entry")
    ap.add_argument("--force", action="store_true", help="re-score already-scored postings")
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
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"), port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"), user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )

    with conn, conn.cursor() as cur:
        rows = fetch_candidates(cur, args.seniority, args.limit, args.force,
                                args.table, ACTIVE_PROFILE, args.min_salary)
        log.info("%s roles to score with %s [profile=%s, table=%s]",
                 len(rows), MODEL, ACTIVE_PROFILE, args.table)
        if not rows:
            log.info("Nothing to do - everything current is already scored.")
            return

        results, written = [], 0
        consecutive_failures = 0
        FLUSH_EVERY = 20
        # Opus 5 list price, plus the 0.1x rate on cache reads.
        RATE_IN, RATE_CACHE_READ, RATE_OUT = 5/1e6, 0.5/1e6, 25/1e6
        cost = 0.0
        for i, row in enumerate(rows, 1):
            try:
                a, usage = assess(client, row)
                consecutive_failures = 0
            except anthropic.APIStatusError as exc:
                consecutive_failures += 1
                # Log the actual message, not just the status. A bare "API
                # error 400" is useless: billing exhaustion, a malformed
                # request and an unsupported parameter all look identical.
                detail = getattr(exc, "message", None) or str(exc)
                log.error("  [%s/%s] %s - HTTP %s: %s",
                          i, len(rows), row[3][:40], exc.status_code, detail[:200])

                # 4xx other than rate limiting will not fix itself by trying
                # the next row. Exhausted credit produced 352 identical
                # failures over ten minutes before this guard existed.
                if exc.status_code in (400, 401, 403) and consecutive_failures >= 3:
                    log.error("Aborting: %s consecutive HTTP %s failures. "
                              "This is an account or request problem, not bad data. "
                              "Rows scored so far are already saved.",
                              consecutive_failures, exc.status_code)
                    break
                continue

            results.append((
                row[0], row[1], a.eligibility, a.eligibility_reason, a.sponsorship_signal,
                a.visa_reasoning, a.fit_score, a.fit_reasoning, a.tech_stack, a.concerns,
                MODEL, _hash(row[8]), ACTIVE_PROFILE, None,
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
                execute_values(cur, UPSERT, [r[:13] + ("NOW()",) for r in results], page_size=100)
                conn.commit()
                written += len(results)
                results = []

        if results:
            execute_values(cur, UPSERT, [r[:13] + ("NOW()",) for r in results], page_size=100)
            conn.commit()
            written += len(results)

    conn.close()
    log.info("Scored %s postings. Cost $%.3f ($%.4f each).",
             written, cost, cost / max(written, 1))


if __name__ == "__main__":
    main()
