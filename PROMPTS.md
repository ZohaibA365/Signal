# Prompts

What was asked for, in the words it was asked in, and what came of it.

Kept because the reasoning behind a change is usually more useful than the
change, and a diff does not record what the person actually wanted. Newest last.

---

## 2026-10-15 — a public demo, without exposing real data

> The tracker is private, so recruiters can't see it. Fix that without exposing
> my real data:
> 1. Add 3–4 screenshots and one short GIF (browse → save a posting → approve a
>    draft) to the tracker README, using fake demo data, not my real applications
>    or drafts.
> 2. Add a public read-only demo: a separate demo login (credentials shown on the
>    login page) that sees only seeded fake applications, notes and drafts, and
>    cannot see or change my real ones. Prove the isolation with a spec. Writes in
>    demo mode either do nothing or reset nightly.
> 3. Tell me the free-tier cold start (~40s) is still there, and what it costs to
>    remove it.
> Tests, push, check CI, log this prompt.
>
> also make sure nothing anything recruiters see has anything related to
> recruiters. This is a real website for people cant seem like something I built
> just for skills or to get a job.

**What it produced.** `AddDemoMode` — ownership on `job_applications` and
`outreach_approvals`, a `demo` flag on users, and a `demo_drafts` table. Every
query scoped to the signed-in account. Writes from the sample account refused in
`ApplicationController` before any action runs, so a forgotten view, a stale form
and a hand-written request all hit the same wall. Sample drafts live in their own
table because `public.outreach_tracker` holds real unsent emails and is read-only
to this app anyway.

`spec/requests/demo_isolation_spec.rb`, 18 examples across what the sample account
cannot read and cannot write. Verified by deliberately unscoping the tracker query
and the draft source and confirming the spec went red both times — an isolation
spec that cannot fail is decoration.

Screenshots and the GIF are captured by driving the real app with Playwright,
signed in as the sample account, so they contain invented data by construction
rather than by someone remembering to scrub them.

**On the last paragraph.** The instruction shaped the copy everywhere. The
sign-in panel says "Have a look around first", not "demo for recruiters". The
banner says "You are in the sample account", not "portfolio mode". A grep for
recruiter, portfolio, resume, showcase across every view returns nothing. A tool
that offers a sample account is a normal thing; a tool that announces it is a
portfolio piece is not.

**Three bugs this turned up, all mine:**

Negative assertions that could not fail. The fixtures were named
`"Owner's Secret Employer"`, and ERB escapes the apostrophe to `&#39;`, so every
`not_to include(...)` passed whether or not the data leaked. Renamed to strings
that survive escaping.

A 500 for the sample account. Suppressing the "no longer in the warehouse"
warning with `posting.nil? && !demo_mode?` let a sample row fall through to an
`elsif` that called `.fit_score` on `nil`.

Buttons labelled `approved` and `rejected` in lowercase. `f.submit "Approve",
value: "approved"` renders an `<input type="submit">`, whose *value* is what the
browser draws. Replaced with `button_tag`, where label and value are separate.
That one was shipped and live, not new.
