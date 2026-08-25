"""
The candidate profile every posting is scored against.

This is plain config, not code that runs logic. Edit it as your skills and
targets change - the fit scores shift accordingly on the next enrichment run.
"""

PROFILE = {
    "name": "Zohaib",
    "citizenship": "Canadian",
    "school": "University of Waterloo",
    "program": "Management Engineering",
    "graduation": "Spring 2028",

    "seeking": {
        "type": "internship / co-op",
        "term": "Winter 2027",
        "months": 4,
        "window": "January 2027 through April 2027",
    },

    "target_roles": [
        "Data Engineer",
        "Analytics Engineer",
        "Data Platform Engineer",
        "Software Engineer (data-focused)",
    ],

    "location": {
        "preference": "United States",
        "remote_ok": True,
        "notes": "Open to any US location. Remote is acceptable.",
    },

    # Everything here is demonstrably true from building this pipeline plus
    # Waterloo coursework. Keep it honest - the scores are only as useful as
    # the input, and inflating skills produces recommendations you can't act on.
    "skills": {
        "strong": ["Python", "SQL", "pandas", "Git"],
        "working": [
            "dbt", "PostgreSQL", "AWS S3", "boto3", "Docker",
            "data modelling", "ETL / ELT pipeline design", "Claude API",
        ],
        "coursework": [
            "statistics", "optimization", "operations research",
            "databases", "machine learning",
        ],
    },

    "evidence": (
        "Built Signal: a daily job-market pipeline that ingests postings from "
        "the Adzuna API into an S3 data lake, loads them into Postgres with "
        "idempotent upserts, transforms them through dbt staging/intermediate/"
        "marts models, and enriches them with an LLM layer."
    ),

    # This is the part that actually determines which roles are reachable.
    #
    # A TN visa requires a completed bachelor's degree. Zohaib graduates in
    # 2028, so TN is NOT available for a Winter 2027 internship - the usual
    # route for a Canadian co-op student is a J-1 Intern/Trainee visa, which
    # the employer must be willing to support (often via a sponsor agency).
    #
    # Two categories of posting are therefore hard blockers regardless of how
    # well the role otherwise matches:
    #   - US citizenship requirements (common at defence contractors)
    #   - active security clearance requirements
    "work_authorization": {
        "needs_sponsorship": True,
        "current_status": "Canadian citizen, enrolled student, no US work authorization",
        "tn_eligible_now": False,
        "tn_eligible_after_graduation": True,
        "likely_route": "J-1 Intern/Trainee visa for a Winter 2027 co-op term",
        "hard_blockers": [
            "requires US citizenship",
            "requires active security clearance",
            "requires existing US work authorization with no sponsorship offered",
        ],
    },
}


def as_prompt_context() -> str:
    """Render the profile as the text block the scoring prompt embeds."""
    p = PROFILE
    wa = p["work_authorization"]
    return f"""CANDIDATE PROFILE

Name: {p['name']}
Citizenship: {p['citizenship']}
School: {p['school']} - {p['program']}
Graduates: {p['graduation']}

Seeking: {p['seeking']['type']}, {p['seeking']['term']} ({p['seeking']['window']}), {p['seeking']['months']} months
Target roles: {', '.join(p['target_roles'])}
Location: {p['location']['notes']}

Strong skills: {', '.join(p['skills']['strong'])}
Working knowledge: {', '.join(p['skills']['working'])}
Relevant coursework: {', '.join(p['skills']['coursework'])}

Portfolio evidence: {p['evidence']}

WORK AUTHORIZATION (critical for scoring)
Status: {wa['current_status']}
Needs sponsorship: yes
TN visa available now: no - TN requires a completed degree, and this candidate
  graduates in {p['graduation']}. TN becomes available after graduation.
Likely route for this term: {wa['likely_route']}
Hard blockers (candidate is categorically ineligible):
{chr(10).join('  - ' + b for b in wa['hard_blockers'])}"""


if __name__ == "__main__":
    print(as_prompt_context())
