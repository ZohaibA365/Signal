"""
The send guard: --mark-sent must refuse unless a person approved this exact text.

Sending is the one action in this system that cannot be undone, and until the Rails
app existed there was nothing to approve against - agent/tools.py:41-49 specified
the shape and left a TODO. These tests cover the branch for every way it can refuse,
because the refusals are the feature.

Driven through a fake cursor rather than a live database. The five outcomes are
decided entirely by what the APPROVAL query returns, so a stub makes each one
reachable in a line, with no schema to create and nothing to clean up. The halves
that genuinely need Postgres are covered elsewhere:

  - that the SQL fingerprint expression agrees with the Ruby one, byte for byte, is
    asserted in tracker/spec/models/outreach_approval_spec.rb against a real server
  - that an approval goes stale when the agent redrafts is asserted in
    tracker/spec/requests/approval_flow_spec.rb
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"


def _load_show_drafts():
    """Import agent/show_drafts.py without importing the whole agent package."""
    if str(AGENT) not in sys.path:
        sys.path.insert(0, str(AGENT))
    spec = importlib.util.spec_from_file_location("show_drafts", AGENT / "show_drafts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


show_drafts = _load_show_drafts()

KEY = "company_board:workday:R_1509223"


class FakeCursor:
    """
    Enough of a cursor to drive mark_sent.

    `approval_row` is what the APPROVAL query returns: (status, decision, current).
    `rowcount` is what the UPDATE reports. Both are set per test, which is the whole
    point - each one makes exactly one branch reachable.
    """

    def __init__(self, approval_row, update_rowcount=1, raise_on_approval=None):
        self.approval_row = approval_row
        self.rowcount = update_rowcount
        self.raise_on_approval = raise_on_approval
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        if "outreach_approvals" in sql and sql.lstrip().upper().startswith("SELECT"):
            if self.raise_on_approval:
                raise self.raise_on_approval
            self._next = self.approval_row

    def fetchone(self):
        return self._next


def _mark(**kwargs):
    cur = FakeCursor(**kwargs)
    with pytest.raises(SystemExit) as exc:
        show_drafts.mark_sent(cur, KEY)
    return str(exc.value), cur


class TestItRefusesAndSaysWhy:
    """
    Four refusals, four different sentences.

    One message covering all of them would send a person looking at the wrong
    thing - the mistake service/app.py made when a dead planner was reported as
    "the run could not be completed", which was true of the planner and false of
    everything needed to write the email.
    """

    def test_a_malformed_job_id_is_named_as_such(self):
        # split_job_id rejects it before any query runs.
        cur = FakeCursor(approval_row=None)
        with pytest.raises(SystemExit) as exc:
            show_drafts.mark_sent(cur, "not-a-key")
        assert "not in the form" in str(exc.value)
        assert cur.statements == [], "it must not query on a malformed id"

    def test_no_draft_at_all(self):
        message, _ = _mark(approval_row=None)
        assert "no draft at all" in message

    def test_wrong_status_names_the_status(self):
        message, _ = _mark(approval_row=("email_sent", "approved", True))
        assert "email_sent" in message
        assert "not 'email_drafted'" in message

    def test_never_approved_points_at_the_tracker_app(self):
        message, _ = _mark(approval_row=("email_drafted", None, None))
        assert "has not been approved" in message
        assert "tracker" in message.lower()

    def test_rejected_is_not_approved(self):
        message, _ = _mark(approval_row=("email_drafted", "rejected", None))
        assert "rejected" in message
        assert "not approved" in message

    def test_a_redraft_after_approval_is_refused_as_stale(self):
        message, _ = _mark(approval_row=("email_drafted", "approved", False))
        assert "changed after it was approved" in message
        assert "Re-approve" in message

    def test_an_unreadable_approvals_table_refuses_rather_than_sends(self):
        """
        tracker.outreach_approvals lives in a schema this script does not own. If it
        cannot be read - revoked grant, dropped schema, a Rails app never deployed -
        the only safe answer is to refuse. Falling back to "no approval needed"
        would turn a permissions problem into an unapproved send.
        """
        message, _ = _mark(approval_row=None, raise_on_approval=RuntimeError("permission denied"))
        assert "cannot read tracker.outreach_approvals" in message
        assert "Nothing was marked as sent" in message


class TestItAllowsOnlyTheApprovedCurrentDraft:
    def test_an_approved_and_current_draft_is_marked_sent(self):
        cur = FakeCursor(approval_row=("email_drafted", "approved", True), update_rowcount=1)
        show_drafts.mark_sent(cur, KEY)  # must not raise

        update = [s for s in cur.statements if s.upper().startswith("UPDATE")]
        assert len(update) == 1, "exactly one UPDATE"
        assert "status = 'email_sent'" in update[0]

    def test_the_update_still_carries_the_guard_itself(self):
        """
        The branches above already checked approval, so the EXISTS clause in the
        UPDATE is redundant - deliberately. The read and the write are separate
        statements, and this is the one action that cannot be undone, so the write
        re-checks rather than trusting a value fetched a moment earlier.
        """
        cur = FakeCursor(approval_row=("email_drafted", "approved", True))
        show_drafts.mark_sent(cur, KEY)

        update = next(s for s in cur.statements if s.upper().startswith("UPDATE"))
        assert "EXISTS" in update
        assert "tracker.outreach_approvals" in update
        assert "a.decision = 'approved'" in update
        assert "draft_fingerprint" in update
        assert "status = 'email_drafted'" in update, "the status guard must survive too"

    def test_a_lost_race_is_reported_rather_than_claimed(self):
        """rowcount 0 means something changed between the read and the write."""
        message, _ = _mark(approval_row=("email_drafted", "approved", True), update_rowcount=0)
        assert "no draft awaiting a send" in message


class TestTheCanonicalFormIsSharedWithRails:
    """
    The fingerprint is taken over a canonical form, and Ruby and SQL must agree on
    what that is. tracker/spec/models/outreach_approval_spec.rb asserts they produce
    identical hashes against a real Postgres; this asserts the SQL has not quietly
    lost a step, which would be the half of that pair this suite can see.
    """

    def test_it_collapses_crlf_and_bare_cr_then_strips(self):
        sql = show_drafts.CANONICAL.format(col="x")
        assert "chr(13) || chr(10)" in sql, "CRLF must collapse to LF"
        assert "chr(13)" in sql, "a bare CR must collapse too"
        assert sql.startswith("btrim("), "leading and trailing whitespace must go"

    def test_the_fingerprint_is_hex_sha256_over_that_form(self):
        fp = show_drafts.FINGERPRINT.format(col="x")
        assert "sha256" in fp
        assert "'hex'" in fp
        assert "btrim" in fp, "the fingerprint must hash the canonical form, not the raw column"

    def test_every_query_that_compares_a_fingerprint_uses_it(self):
        source = (AGENT / "show_drafts.py").read_text(encoding="utf-8")
        # No query may hash draft_email directly - that is the bug this replaced.
        assert "sha256(t.draft_email::bytea)" not in source
        assert "sha256(outreach_tracker.draft_email::bytea)" not in source
        assert source.count("FINGERPRINT.format(") == 3, (
            "APPROVAL, PENDING and the UPDATE must all use the shared expression"
        )
