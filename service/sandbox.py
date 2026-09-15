"""
The sandbox the public runs write to, and the role that confines them to it.

Public traffic must not be able to touch the real application tracker. The
mechanism is two things working together, and neither is a code convention that a
bug could step around:

  1. A `demo` schema holding its own copy of outreach_tracker. A connection that
     sets `search_path TO demo, public` resolves outreach_tracker to the sandbox
     copy, while raw_postings, apply_queue and dim_company still resolve from
     public - because demo does not contain them. The agent's SQL is unchanged.

  2. A `signal_demo` role with SELECT on the warehouse and write access to exactly
     one table. If the web layer had a bug that aimed a write at the real tracker,
     Postgres refuses it. The isolation is enforced by the database rather than by
     the care of whoever edits the service next.

Run once, by hand, with the owner credentials:

    python service/sandbox.py --create        # schema, table, role, grants
    python service/sandbox.py --seed          # rows for the rails to fire against
    python service/sandbox.py --verify        # prove the role cannot reach production
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "storage"))

from db import connect  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("sandbox")

DEMO_ROLE = "signal_demo"

# Tables the agent reads. SELECT only, and listed explicitly rather than granted
# across the schema: a future table holding something private should not become
# readable by the public service just because it was created.
READABLE = ("raw_postings", "apply_queue", "dim_company", "job_enrichment",
            "posting_technologies", "int_company_sponsorship", "market_demand",
            "dim_technology", "hist_coverage", "company_identity")

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS demo;

-- The same shape as public.outreach_tracker. Deliberately a copy rather than a
-- view or a partition: the point is that writes here cannot reach there.
CREATE TABLE IF NOT EXISTS demo.outreach_tracker (
    source            TEXT NOT NULL,
    job_id            TEXT NOT NULL,
    company_name      TEXT,
    job_title         TEXT,
    status            TEXT NOT NULL DEFAULT 'not_contacted',
    draft_email       TEXT,
    draft_connection  TEXT,
    draft_followup    TEXT,
    draft_source      TEXT,
    notes             TEXT,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, job_id)
);

-- Spend and usage, kept in the database rather than in the process, so a restart
-- or a redeploy cannot reset the day's budget. A counter that forgets is not a
-- budget.
CREATE TABLE IF NOT EXISTS demo.usage_ledger (
    day        DATE    NOT NULL,
    client     TEXT    NOT NULL,       -- an IP, or '*' for the whole day
    runs       INTEGER NOT NULL DEFAULT 0,
    cost_usd   NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (day, client)
);
"""


def create(cur, password: str) -> None:
    cur.execute(SCHEMA_SQL)

    # CREATE ROLE is not idempotent, and IF NOT EXISTS does not exist for it.
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (DEMO_ROLE,))
    if cur.fetchone():
        cur.execute(f'ALTER ROLE {DEMO_ROLE} WITH LOGIN PASSWORD %s', (password,))
        log.info("role %s already existed; password reset", DEMO_ROLE)
    else:
        cur.execute(f'CREATE ROLE {DEMO_ROLE} WITH LOGIN PASSWORD %s', (password,))
        log.info("created role %s", DEMO_ROLE)

    cur.execute(f"GRANT USAGE ON SCHEMA public TO {DEMO_ROLE}")
    cur.execute(f"GRANT USAGE ON SCHEMA demo TO {DEMO_ROLE}")
    for table in READABLE:
        cur.execute(f"GRANT SELECT ON public.{table} TO {DEMO_ROLE}")
    cur.execute(f"GRANT SELECT, INSERT, UPDATE ON demo.outreach_tracker TO {DEMO_ROLE}")
    cur.execute(f"GRANT SELECT, INSERT, UPDATE ON demo.usage_ledger TO {DEMO_ROLE}")

    # Said out loud rather than assumed: no write anywhere in public, ever.
    cur.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES "
                f"IN SCHEMA public FROM {DEMO_ROLE}")

    # A grant belongs to the table, not to the name, so dropping and recreating a
    # table takes its grants with it - and that is exactly what dbt does to the
    # marts every night. The public page therefore worked all day and began
    # answering "permission denied for table dim_company" the moment the pipeline
    # rebuilt it, with nothing in the service having changed.
    #
    # Default privileges close it: they apply to tables that do not exist yet,
    # granted for the role that creates them, so tomorrow's dim_company is readable
    # the moment dbt makes it. Tied to the current user because default privileges
    # are per-creator, and this runs as the same owner dbt does.
    cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT ON TABLES TO {DEMO_ROLE}")
    log.info("granted read on %s warehouse table(s), write on demo only, and on "
             "public tables created from now on", len(READABLE))


def seed(cur) -> None:
    """
    Rows for the duplicate rail to fire against.

    Real postings, so the sandbox agrees with the warehouse about what exists -
    a seeded row pointing at a job_id absent from raw_postings would make the
    duplicate scenario fail the existence check first, and demonstrate the wrong
    rail.
    """
    cur.execute("""
        SELECT source, job_id, company_name, job_title
        FROM apply_queue
        WHERE fit_score >= 75 AND link_tier = 'direct'
        ORDER BY fit_score DESC, company_name
        LIMIT 3
    """)
    rows = cur.fetchall()
    if not rows:
        log.warning("no high-scoring postings to seed from")
        return
    for i, (source, job_id, company, title) in enumerate(rows):
        status = "email_sent" if i == 0 else "email_drafted"
        cur.execute("""
            INSERT INTO demo.outreach_tracker
                (source, job_id, company_name, job_title, status, notes)
            VALUES (%s, %s, %s, %s, %s, 'seeded so the duplicate rail has something real')
            ON CONFLICT (source, job_id) DO UPDATE SET status = EXCLUDED.status
        """, (source, job_id, company, title, status))
        log.info("  seeded %s at %s", company, status)


# How long a visitor's run stays in the sandbox before it is cleared.
#
# Without this the demo degrades into uselessness on its own: every company a
# visitor tries becomes "already contacted", so within a week the only outcome
# anybody sees is the duplicate rail and the happy path is unreachable. Seeded rows
# are exempt, because the duplicate rail needs something permanent to fire against.
#
# Two hours was far too long, and the reason is that this table is shared by
# everyone. One person trying SpaceX made SpaceX unavailable to every other visitor
# for the rest of the afternoon - and what they saw was not an explanation, it was
# their own posting being refused with "already contacted" by a tool they had never
# used before. Fifteen minutes still demonstrates the rail to somebody who runs the
# same company twice in a row, which is the only time it is worth demonstrating to
# them, and the seeded rows demonstrate it on purpose and permanently.
VISITOR_ROW_TTL_MINUTES = int(os.getenv("AGENT_VISITOR_ROW_TTL_MINUTES", "15"))

# Reset rather than delete, and that is not a stylistic choice. The demo role has
# SELECT, INSERT and UPDATE on this table and no DELETE, so the first version of
# this failed with "permission denied for table outreach_tracker" - the confinement
# working exactly as intended, against its own author. Granting DELETE to make the
# sweep work would have widened the role to fix a problem an UPDATE solves.
#
# The rows stay, bounded by the number of companies anyone ever tries, which is at
# most the size of the corpus and costs nothing.
SWEEP_SQL = """
    UPDATE demo.outreach_tracker
       SET status = 'not_contacted',
           draft_email = NULL, draft_connection = NULL, draft_followup = NULL,
           draft_source = NULL, notes = 'expired visitor run, reset',
           updated_at = now()
     WHERE updated_at < now() - make_interval(mins => %s)
       AND status <> 'not_contacted'
       AND coalesce(notes, '') NOT LIKE 'seeded%%'
"""


def sweep(cur, minutes: int = VISITOR_ROW_TTL_MINUTES) -> int:
    """Return expired visitor rows to their starting state. Safe to call per run."""
    cur.execute(SWEEP_SQL, (minutes,))
    return cur.rowcount


def verify(password: str) -> None:
    """Prove the confinement rather than trusting it."""
    import psycopg2

    url = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("no database URL to derive a demo connection from")
    demo_url = _as_role(url, DEMO_ROLE, password)

    conn = psycopg2.connect(demo_url, connect_timeout=30)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO demo, public")
            cur.execute("SELECT count(*) FROM raw_postings")
            log.info("  reads the warehouse: %s postings visible", f"{cur.fetchone()[0]:,}")

            cur.execute("SELECT count(*) FROM outreach_tracker")
            log.info("  outreach_tracker resolves to the sandbox: %s row(s)",
                     cur.fetchone()[0])

            try:
                cur.execute("INSERT INTO public.outreach_tracker (source, job_id, status) "
                            "VALUES ('probe', 'probe', 'not_contacted')")
            except psycopg2.errors.InsufficientPrivilege:
                log.info("  refused a write to public.outreach_tracker, as it must")
            else:
                raise SystemExit("THE DEMO ROLE CAN WRITE TO PRODUCTION - do not deploy")

            try:
                cur.execute("UPDATE public.raw_postings SET company_name = company_name")
            except psycopg2.errors.InsufficientPrivilege:
                log.info("  refused a write to public.raw_postings, as it must")
            else:
                raise SystemExit("THE DEMO ROLE CAN WRITE TO THE WAREHOUSE - do not deploy")
    finally:
        conn.close()


def _as_role(url: str, role: str, password: str) -> str:
    """Swap the credentials in a connection URL, leaving the rest intact."""
    import urllib.parse as up

    parsed = up.urlparse(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return up.urlunparse((
        parsed.scheme, f"{role}:{up.quote(password)}@{host}{port}",
        parsed.path, parsed.params, parsed.query, parsed.fragment))


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up the public sandbox")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--password", help="demo role password (generated if absent)")
    args = ap.parse_args()
    if not (args.create or args.seed or args.verify):
        raise SystemExit("choose --create, --seed or --verify")

    password = args.password or os.getenv("DEMO_DB_PASSWORD") or secrets.token_urlsafe(24)

    if args.create or args.seed:
        conn = connect()
        try:
            with conn, conn.cursor() as cur:
                if args.create:
                    create(cur, password)
                if args.seed:
                    seed(cur)
        finally:
            conn.close()

    if args.create and not (args.password or os.getenv("DEMO_DB_PASSWORD")):
        # Printed once, never stored. It goes into the host's environment.
        print(f"\nDEMO_DB_PASSWORD={password}\n")
        print("Put that in the service's environment. It is not written anywhere.")

    if args.verify:
        verify(password)


if __name__ == "__main__":
    main()
