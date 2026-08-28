"""
Candidate profiles that postings are scored against.

Plain config, not logic. Signal supports more than one profile because the
pipeline is domain-agnostic: the same ingestion, modelling and scoring layers
work for any role and any market. Only this file changes.

Select with the SIGNAL_PROFILE environment variable:

    SIGNAL_PROFILE=cobol python ai_layer/enrich.py --force
    SIGNAL_PROFILE=student python ai_layer/enrich.py

Adding a profile does not require touching any other module.
"""

from __future__ import annotations

import os

PROFILES: dict[str, dict] = {

    # ------------------------------------------------------------------
    "cobol": {
        "label": "COBOL / mainframe programmer",
        "citizenship": "Canadian",
        "seeking": {
            "type": "full-time permanent",
            "notes": "Open to contract if the rate is strong.",
        },
        "target_roles": [
            "COBOL Programmer",
            "COBOL Developer",
            "Mainframe Developer",
            "Mainframe Applications Engineer",
        ],
        "location": {
            "countries": ["United States", "Canada"],
            "remote_ok": True,
            "notes": "Either the US or Canada. Remote acceptable.",
        },
        "compensation": {
            # The hard filter the candidate set. Note the currency trap below.
            "minimum": 80000,
            "notes": (
                "Must clear 80,000. Canadian postings quote CAD and US postings "
                "quote USD; they are not interchangeable and 80k CAD is roughly "
                "58k USD at recent rates."
            ),
        },
        "skills": {
            "strong": ["COBOL", "JCL", "CICS", "Db2", "VSAM", "mainframe (z/OS)"],
            "working": ["batch processing", "SQL", "TSO/ISPF", "production support"],
            "coursework": [],
        },
        "evidence": "Mainframe applications development and support experience.",
        "work_authorization": {
            "needs_sponsorship": True,
            "current_status": "Canadian citizen",
            "notes": (
                "No sponsorship needed for Canadian roles. US roles require "
                "sponsorship or TN status. TN is available to Canadians holding a "
                "completed bachelor's degree in a qualifying field, processed at "
                "the border with no lottery and at no cost to the employer - worth "
                "flagging on any US posting that mentions work authorisation."
            ),
            "hard_blockers": [
                "requires US citizenship",
                "requires active security clearance",
            ],
        },
    },

    # ------------------------------------------------------------------
    "student": {
        "label": "Data engineering intern (Winter 2027)",
        "citizenship": "Canadian",
        "school": "University of Waterloo",
        "program": "Management Engineering",
        "graduation": "Spring 2028",
        "seeking": {
            "type": "internship / co-op",
            "term": "Winter 2027",
            "months": 4,
            "notes": "January 2027 through April 2027.",
        },
        "target_roles": [
            "Data Engineer", "Analytics Engineer",
            "Data Platform Engineer", "Software Engineer (data-focused)",
        ],
        "location": {
            "countries": ["United States"],
            "remote_ok": True,
            "notes": "Open to any US location. Remote is acceptable.",
        },
        "compensation": {"minimum": None, "notes": "Not a filter for an internship."},
        "skills": {
            "strong": ["Python", "SQL", "pandas", "Git"],
            "working": ["dbt", "PostgreSQL", "AWS S3", "boto3", "Docker",
                        "data modelling", "ETL / ELT pipeline design", "Claude API"],
            "coursework": ["statistics", "optimization", "operations research",
                           "databases", "machine learning"],
        },
        "evidence": (
            "Built Signal: a daily job-market pipeline ingesting postings into an "
            "S3 data lake, loading them into Postgres with idempotent upserts, "
            "transforming through dbt, and enriching with an LLM layer."
        ),
        "work_authorization": {
            "needs_sponsorship": True,
            "current_status": "Canadian citizen, enrolled student, no US work authorization",
            "notes": (
                "TN requires a completed degree and this candidate graduates in "
                "2028, so TN is NOT available for a Winter 2027 term. The usual "
                "route is a J-1 Intern/Trainee visa the employer must support."
            ),
            "hard_blockers": [
                "requires US citizenship",
                "requires active security clearance",
                "requires existing US work authorization with no sponsorship offered",
            ],
        },
    },
}

ACTIVE = os.getenv("SIGNAL_PROFILE", "cobol")
if ACTIVE not in PROFILES:
    raise SystemExit(f"Unknown SIGNAL_PROFILE {ACTIVE!r}. Options: {', '.join(PROFILES)}")

PROFILE = PROFILES[ACTIVE]


def as_prompt_context() -> str:
    """Render the active profile as the text block the scoring prompt embeds."""
    p = PROFILE
    wa = p["work_authorization"]
    comp = p["compensation"]
    sk = p["skills"]

    lines = [
        "CANDIDATE PROFILE",
        "",
        f"Profile: {p['label']}",
        f"Citizenship: {p['citizenship']}",
    ]
    if p.get("school"):
        lines.append(f"School: {p['school']} - {p['program']}, graduates {p['graduation']}")

    seeking = p["seeking"]
    term = f", {seeking['term']}" if seeking.get("term") else ""
    lines += [
        f"Seeking: {seeking['type']}{term}. {seeking.get('notes', '')}".rstrip(),
        f"Target roles: {', '.join(p['target_roles'])}",
        f"Location: {p['location']['notes']}",
    ]
    if comp.get("minimum"):
        lines.append(f"Minimum compensation: {comp['minimum']:,}. {comp['notes']}")

    lines += [
        "",
        f"Strong skills: {', '.join(sk['strong'])}",
        f"Working knowledge: {', '.join(sk['working'])}",
    ]
    if sk.get("coursework"):
        lines.append(f"Relevant coursework: {', '.join(sk['coursework'])}")

    lines += [
        f"Evidence: {p['evidence']}",
        "",
        "WORK AUTHORIZATION (critical for scoring)",
        f"Status: {wa['current_status']}",
        wa["notes"],
        "Hard blockers (candidate is categorically ineligible):",
    ]
    lines += [f"  - {b}" for b in wa["hard_blockers"]]
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"[active profile: {ACTIVE}]\n")
    print(as_prompt_context())
