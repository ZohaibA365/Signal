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

PENDING = """
    SELECT source || ':' || job_id AS job_id, company_name, job_title,
           draft_source, draft_email, draft_connection, draft_followup, updated_at
    FROM outreach_tracker
    WHERE status = %(status)s
      AND (%(company)s IS NULL OR company_name = %(company)s)
    ORDER BY updated_at DESC
"""


def show(cur, args) -> int:
    cur.execute(PENDING, {"status": args.status, "company": args.company})
    rows = cur.fetchall()
    if not rows:
        log.info("nothing at status %r%s", args.status,
                 f" for {args.company}" if args.company else "")
        return 0

    column = {"email": 4, "connection": 5, "followup": 6}[args.variant]
    for job_id, company, title, source, *drafts, updated in rows:
        text = (job_id, company, title, source, *drafts, updated)[column]
        print("\n" + "=" * 78)
        print(f"{company} - {title}")
        # Who wrote it is printed with the draft rather than buried, because it
        # changes how closely the thing below should be read.
        print(f"{job_id}    written by the {source}, {updated:%Y-%m-%d %H:%M}")
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


def write_page(rows, column, path: str) -> None:
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
    for job_id, company, title, source, *drafts, updated in rows:
        text = (job_id, company, title, source, *drafts, updated)[column]
        parts.append(
            f'<div class="d">'
            f'<button onclick="navigator.clipboard.writeText('
            f'this.parentNode.querySelector(&quot;pre&quot;).innerText)">copy</button>'
            f'<p class="h">{_html.escape(company or "")} '
            f'<span class="tag">{_html.escape(source or "?")}</span></p>'
            f'<p class="m">{_html.escape(title or "")}<br>{_html.escape(job_id)}</p>'
            f'<pre>{_html.escape(text or "(no draft stored)")}</pre></div>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def mark_sent(cur, job_id: str) -> None:
    """
    Record that a person sent one. Deliberately not something the agent can do.

    This is the transition the agent is forbidden to make, and it lives in a
    separate program run by hand for that reason: 'email_sent' is a claim about the
    world, and only the person who acted can make it truthfully.
    """
    parts = split_job_id(job_id)
    if parts is None:
        raise SystemExit(f"{job_id!r} is not in the form 'source:job_id'")
    cur.execute("""
        UPDATE outreach_tracker SET status = 'email_sent', updated_at = NOW()
        WHERE source = %s AND job_id = %s AND status = 'email_drafted'
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
                column = {"email": 4, "connection": 5, "followup": 6}[args.variant]
                write_page(rows, column, args.html)
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
