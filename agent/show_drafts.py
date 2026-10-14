"""
Read the drafts back out, ready to copy into an email client.

The agent writes drafts into outreach_tracker and nothing read them, which left
the whole thing unusable in practice: a tool whose output can only be reached with
a hand-written SQL query is a tool nobody uses. This is the other half.

Sending stays manual and out of this program. Drafts are printed for a person to
read, edit and send, and marking one as sent is a separate deliberate act:

    python agent/show_drafts.py                      # everything awaiting a send
    python agent/show_drafts.py --html               # ...as a page, opened in a browser
    python agent/show_drafts.py --company "TD Bank"
    python agent/show_drafts.py --variant connection
    python agent/show_drafts.py --mark-sent company_board:lever:abc123

Most people should use ./outreach at the repository root instead, which calls this
with the right interpreter from the right directory.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("storage", "agent"):
    sys.path.insert(0, os.path.join(_ROOT, _sub))

from db import connect  # noqa: E402
from schemas import split_job_id  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("show_drafts")

# The canonical form a draft fingerprint is taken over.
#
# It MUST match OutreachApproval.canonical in tracker/app/models/outreach_approval.rb
# exactly: CRLF (and bare CR) collapsed to LF, then leading and trailing whitespace
# stripped. HTML form submission normalises textarea line breaks to CRLF while the
# agent writes LF, so without this an approval of an UNEDITED draft would never
# match and every approval would read as stale - a bug that looks exactly like the
# staleness check working correctly.
#
# Defined once and interpolated, rather than written out at each of the three places
# it is needed, because three copies of a hashing rule is three chances to drift.
CANONICAL = (
    "btrim(replace(replace({col}, chr(13) || chr(10), chr(10)), chr(13), chr(10)),"
    " ' ' || chr(9) || chr(10))"
)
FINGERPRINT = "encode(sha256((" + CANONICAL + ")::bytea), 'hex')"


# NOTE ON COLUMN ORDER: show() and write_page() unpack this row by name, so a new
# column is safe to append at the END. It is NOT safe to insert one in the middle:
# both functions pick the draft variant out of the (email, connection, followup)
# triple by position, and an earlier insertion would print the wrong variant.
#
# The starred unpacking these functions used before - `*drafts, updated` - absorbed
# any new column silently and shifted everything after it, which is how adding the
# approval column below first handed a timestamp format string a plain string.
PENDING = f"""
    SELECT t.source || ':' || t.job_id AS job_id, t.company_name, t.job_title,
           t.draft_source, t.draft_email, t.draft_connection, t.draft_followup,
           t.updated_at,
           CASE
             WHEN a.decision IS NULL THEN 'not reviewed'
             WHEN a.decision <> 'approved' THEN a.decision
             WHEN a.draft_fingerprint = {FINGERPRINT.format(col='t.draft_email')} THEN 'approved'
             ELSE 'approved, now stale'
           END AS approval
    FROM outreach_tracker t
    LEFT JOIN tracker.outreach_approvals a
           ON a.source = t.source AND a.job_id = t.job_id
    WHERE t.status = %(status)s
      AND (%(company)s IS NULL OR t.company_name = %(company)s)
    ORDER BY t.updated_at DESC
"""


def show(cur, args) -> int:
    cur.execute(PENDING, {"status": args.status, "company": args.company})
    rows = cur.fetchall()
    if not rows:
        log.info("nothing at status %r%s", args.status,
                 f" for {args.company}" if args.company else "")
        return 0

    # Unpacked by NAME, not with a starred catch-all. The previous form was
    # `job_id, company, title, source, *drafts, updated`, which silently absorbed
    # any new column into `drafts` and shifted `updated` - so adding the approval
    # column below made a timestamp format string receive a string. Explicit names
    # fail loudly on a shape change instead.
    variant_column = {"email": 0, "connection": 1, "followup": 2}[args.variant]
    for (job_id, company, title, source,
         draft_email, draft_connection, draft_followup,
         updated, approval) in rows:
        text = (draft_email, draft_connection, draft_followup)[variant_column]
        print("\n" + "=" * 78)
        print(f"{company} - {title}")
        # Who wrote it is printed with the draft rather than buried, because it
        # changes how closely the thing below should be read. The approval state
        # goes here too: it decides whether --mark-sent will accept this one.
        print(f"{job_id}    written by the {source}, {updated:%Y-%m-%d %H:%M}")
        print(f"{'':<{len(job_id)}}    approval: {approval}")
        print("=" * 78)
        print(text or "(no draft stored for this variant)")

    print("\n" + "-" * 78)
    print(f"{len(rows)} draft(s). Send one yourself, then record it:")
    print(f"  python agent/show_drafts.py --mark-sent {rows[0][0]}")

    companies = {r[1] for r in rows}
    if len(companies) < len(rows):
        # The email is company-level, so two postings at one employer produce
        # near-identical messages. Sending both to the same person is the failure
        # mode worth warning about at exactly the moment they are being read.
        print(f"\n  NOTE: these {len(rows)} drafts cover only {len(companies)} "
              f"companies. Send one per company, not one per posting.")
    return len(rows)


PAGE_HEAD = """<!doctype html><meta charset="utf-8">
<title>Outreach drafts</title>
<style>
 body{font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      max-width:760px;margin:40px auto;padding:0 20px;color:#111;background:#fafafa}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#666;font-size:13px;margin:0 0 28px}
 .d{background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:18px;margin:0 0 18px}
 .h{font-size:14px;font-weight:600;margin:0 0 2px}
 .m{color:#777;font-size:12px;margin:0 0 12px;font-family:ui-monospace,monospace}
 pre{white-space:pre-wrap;margin:0;font:14px/1.6 inherit}
 .tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;
      background:#eee;color:#555;margin-left:6px;font-family:ui-monospace,monospace}
 .warn{background:#fff6e5;border:1px solid #f0d9a8;border-radius:8px;padding:12px;
       font-size:13px;margin:0 0 20px}
 button{font:12px inherit;padding:4px 10px;border:1px solid #ccc;background:#fff;
        border-radius:6px;cursor:pointer;float:right}
</style>
<h1>Outreach drafts</h1>
<p class="sub">Copy one, send it yourself, then run the command shown under it.</p>
"""


def write_page(rows, variant_column, path: str) -> None:
    """
    The same drafts as a page, because copying out of a terminal is miserable.

    Deliberately a local file rather than anything served: these are unsent messages
    in one person's name, and they belong on that person's disk.
    """
    import html as _html

    companies = {r[1] for r in rows}
    parts = [PAGE_HEAD]
    if len(companies) < len(rows):
        parts.append(f'<div class="warn"><b>{len(rows)} drafts, {len(companies)} '
                     f'companies.</b> The message is written about the company, not '
                     f'the specific role, so send one per company.</div>')
    # Unpacked by name for the same reason as show(): a starred catch-all silently
    # absorbs any new column and shifts everything after it.
    for (job_id, company, title, source,
         draft_email, draft_connection, draft_followup,
         updated, approval) in rows:
        text = (draft_email, draft_connection, draft_followup)[variant_column]
        parts.append(
            f'<div class="d">'
            f'<button onclick="navigator.clipboard.writeText('
            f'this.parentNode.querySelector(&quot;pre&quot;).innerText)">copy</button>'
            f'<p class="h">{_html.escape(company or "")} '
            f'<span class="tag">{_html.escape(source or "?")}</span> '
            # The approval state belongs next to the text, because it decides
            # whether --mark-sent will accept this draft at all.
            f'<span class="tag">{_html.escape(approval or "?")}</span></p>'
            f'<p class="m">{_html.escape(title or "")}<br>{_html.escape(job_id)}'
            f'<br>{updated:%Y-%m-%d %H:%M}</p>'
            f'<pre>{_html.escape(text or "(no draft stored)")}</pre></div>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


# Whether a draft has been approved by a person, and whether that approval still
# covers the text that is there now.
#
# tracker.outreach_approvals is written by the Rails app in tracker/. The approval
# records a sha256 of the exact text approved, so an approval cannot be stretched
# to cover a later redraft: agent/tools.py's upsert uses coalesce, which means a
# draft column keeps its old text rather than being cleared, and the agent can
# therefore change the words under an approval that was given in good faith.
#
# Deliberately a separate table and not a new `status` value. agent/tools.py:41-49
# specifies the shape - "already at 'email_drafted' AND carries an explicit human
# approval marker" - and a new status would break three things: ALREADY_DRAFTED in
# agent/schemas.py is derived by SLICING STATUSES so the order is semantic, the
# UPDATE below guards on status = 'email_drafted' exactly, and draft_is_allowed
# rejects anything outside STATUSES.
APPROVAL = f"""
    SELECT t.status,
           a.decision,
           a.draft_fingerprint = {FINGERPRINT.format(col='t.draft_email')} AS current
    FROM outreach_tracker t
    LEFT JOIN tracker.outreach_approvals a
           ON a.source = t.source AND a.job_id = t.job_id
    WHERE t.source = %s AND t.job_id = %s
"""


def mark_sent(cur, job_id: str) -> None:
    """
    Record that a person sent one. Deliberately not something the agent can do.

    This is the transition the agent is forbidden to make, and it lives in a
    separate program run by hand for that reason: 'email_sent' is a claim about the
    world, and only the person who acted can make it truthfully.

    It now also requires an approval recorded in the Rails app, and says which of
    the four possible reasons it is refusing for. A single "no draft awaiting a
    send" covering all of them would send you looking at the wrong thing - the same
    mistake the service made when a dead planner was reported as "the run could not
    be completed", which was true of the planner and false of everything else.
    """
    parts = split_job_id(job_id)
    if parts is None:
        raise SystemExit(f"{job_id!r} is not in the form 'source:job_id'")

    try:
        cur.execute(APPROVAL, parts)
    except Exception as exc:                                     # noqa: BLE001
        # The approvals table lives in a schema this script does not own. If it
        # cannot be read, refuse rather than fall back to sending unapproved.
        raise SystemExit(
            f"cannot read tracker.outreach_approvals ({type(exc).__name__}), "
            "so approval cannot be confirmed. Nothing was marked as sent."
        ) from exc

    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no draft at all for {job_id}")

    status, decision, approval_is_current = row
    if status != "email_drafted":
        raise SystemExit(f"{job_id} is at status {status!r}, not 'email_drafted'")
    if decision is None:
        raise SystemExit(
            f"{job_id} has not been approved. Approve it in the tracker app first "
            "(Approvals tab); nothing was marked as sent."
        )
    if decision != "approved":
        raise SystemExit(f"{job_id} was {decision}, not approved")
    if not approval_is_current:
        raise SystemExit(
            f"the draft for {job_id} changed after it was approved, so the approval "
            "no longer covers what would be sent. Re-approve it in the tracker app."
        )

    # The EXISTS clause repeats the check the branches above already made. That is
    # on purpose: the read and the write are separate statements, and this is the
    # one action in the system that cannot be undone.
    cur.execute(f"""
        UPDATE outreach_tracker SET status = 'email_sent', updated_at = NOW()
        WHERE source = %s AND job_id = %s AND status = 'email_drafted'
          AND EXISTS (
            SELECT 1 FROM tracker.outreach_approvals a
            WHERE a.source = outreach_tracker.source
              AND a.job_id = outreach_tracker.job_id
              AND a.decision = 'approved'
              AND a.draft_fingerprint = {FINGERPRINT.format(col='outreach_tracker.draft_email')}
          )
    """, parts)
    if cur.rowcount == 0:
        raise SystemExit(f"no draft awaiting a send for {job_id}")
    log.info("%s marked as sent", job_id)


def main() -> None:
    ap = argparse.ArgumentParser(description="Read outreach drafts, and record sends")
    ap.add_argument("--variant", default="email",
                    choices=["email", "connection", "followup"])
    ap.add_argument("--company")
    ap.add_argument("--status", default="email_drafted")
    ap.add_argument("--mark-sent", metavar="JOB_ID")
    ap.add_argument("--html", nargs="?", const="drafts.html", metavar="PATH",
                    help="write the drafts as a page and open it")
    args = ap.parse_args()

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            if args.mark_sent:
                mark_sent(cur, args.mark_sent)
            elif args.html:
                cur.execute(PENDING, {"status": args.status, "company": args.company})
                rows = cur.fetchall()
                if not rows:
                    log.info("no drafts at status %r", args.status)
                    return
                variant_column = {"email": 0, "connection": 1, "followup": 2}[args.variant]
                write_page(rows, variant_column, args.html)
                log.info("wrote %s (%s draft(s))", args.html, len(rows))
                if sys.platform == "darwin":
                    import subprocess
                    subprocess.run(["open", args.html], check=False)
            else:
                show(cur, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
