"""
What stops a public LLM endpoint from becoming an open wallet.

Two controls, and only the second is real protection. A per-visitor cap is polite
and stops casual repetition; anyone who wants to get past it rotates addresses in a
loop. The daily spend ceiling is what actually bounds the damage, so it is the one
kept in the database rather than in the process - a counter that forgets on restart
is not a budget, and a redeploy is exactly when someone would be hammering it.

Both return a reason rather than raising, because the page has to say something
friendly rather than showing an error.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

# Per visitor, per day. Five was set on the assumption that a visitor is one
# person at one address, and that assumption is wrong on exactly the networks this
# page is shared into: a university NAT puts a whole campus behind one address, so
# five was five runs for all of Waterloo, and the author hit the cap testing his
# own site. Twenty costs at most forty cents against a two dollar ceiling, so the
# ceiling below is still what binds - which was always the intent. The per-address
# cap only has to stop someone sitting on the button.
RUNS_PER_CLIENT = int(os.getenv("AGENT_RUNS_PER_IP", "20"))

# The whole service, per day, in dollars. At roughly two cents a run this is about
# a hundred runs. Past it the endpoint stops spending and the page falls back to a
# recorded run, which is indistinguishable to a visitor.
DAILY_BUDGET_USD = float(os.getenv("AGENT_DAILY_BUDGET_USD", "2.00"))

# What one run costs, used to reserve budget BEFORE the model is called. Reserving
# after would let a burst of simultaneous requests all pass a check that each of
# them then invalidates.
ESTIMATED_RUN_USD = float(os.getenv("AGENT_ESTIMATED_RUN_USD", "0.02"))

TOTAL = "*"


def _today() -> date:
    return datetime.now(UTC).date()


# Runs that never call a model. A deterministic draft costs one database query, so
# this is not a spending control - it is only here so a script cannot sit on the
# button. It is deliberately far above anything a person does by hand.
TEMPLATE_RUNS_PER_CLIENT = int(os.getenv("AGENT_TEMPLATE_RUNS_PER_IP", "60"))


def check_and_reserve(cur, client: str) -> tuple[str, str]:
    """
    Decide how this run may happen, and account for it if so.

    Returns "model", "template" or "refused", with a sentence for the page.

    It used to return yes or no, and "no" meant the visitor was shown a recording
    of somebody else's run instead of an email of their own. That conflated two
    different limits. Both of these caps exist to bound what the MODEL costs; the
    deterministic draft needs no model at all, only a query against the warehouse.
    So being over budget does not mean there is nothing to write - it means the
    sentences are assembled rather than generated, from the same verified figures,
    with the visitor's own details in them. Only a database that cannot be reached
    leaves us with nothing true to say.

    One statement per counter, each an upsert that returns the new total, so two
    requests arriving together cannot both read the old value and both proceed.
    The database does the arithmetic; the service never holds a count in memory.
    """
    today = _today()

    cur.execute("""
        INSERT INTO demo.usage_ledger (day, client, runs, cost_usd)
        VALUES (%s, %s, 1, %s)
        ON CONFLICT (day, client) DO UPDATE
            SET runs = demo.usage_ledger.runs + 1,
                cost_usd = demo.usage_ledger.cost_usd + EXCLUDED.cost_usd
        RETURNING runs, cost_usd
    """, (today, TOTAL, ESTIMATED_RUN_USD))
    _, spent = cur.fetchone()

    over_budget = float(spent) > DAILY_BUDGET_USD

    cur.execute("""
        INSERT INTO demo.usage_ledger (day, client, runs, cost_usd)
        VALUES (%s, %s, 1, 0)
        ON CONFLICT (day, client) DO UPDATE
            SET runs = demo.usage_ledger.runs + 1
        RETURNING runs
    """, (today, client))
    runs = cur.fetchone()[0]

    if runs > TEMPLATE_RUNS_PER_CLIENT:
        return "refused", (f"You have run this {TEMPLATE_RUNS_PER_CLIENT} times "
                           f"today, which is the limit. Try again tomorrow.")

    if over_budget or runs > RUNS_PER_CLIENT:
        # Still a real run against real data, and still the visitor's own details.
        # The model is what is unavailable, not the warehouse.
        return "template", ("Today's model budget is used up, so the wording is "
                            "assembled rather than written - the figures are the "
                            "same and they are checked the same way.")

    return "model", ""


def refund(cur, client: str) -> None:
    """
    Give back the reservation when a run did not happen after all.

    Called when the run fails before the model is reached, so a visitor is not
    charged an attempt for the service's own problem.
    """
    today = _today()
    cur.execute("""
        UPDATE demo.usage_ledger
           SET runs = greatest(runs - 1, 0),
               cost_usd = greatest(cost_usd - %s, 0)
         WHERE day = %s AND client = %s
    """, (ESTIMATED_RUN_USD, today, TOTAL))
    cur.execute("""
        UPDATE demo.usage_ledger SET runs = greatest(runs - 1, 0)
         WHERE day = %s AND client = %s
    """, (today, client))


def totals(cur) -> dict:
    """What the page's stats strip reads."""
    cur.execute("""
        SELECT coalesce(sum(runs), 0), coalesce(sum(cost_usd), 0)
        FROM demo.usage_ledger WHERE client = %s
    """, (TOTAL,))
    runs, spent = cur.fetchone()
    cur.execute("SELECT count(*) FROM demo.outreach_tracker WHERE status <> 'not_contacted'")
    return {"runs": int(runs), "spent_usd": round(float(spent), 2),
            "tracked": cur.fetchone()[0]}
