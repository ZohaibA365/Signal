"""
A pasted job link has to work, because it is what people paste.

The box says "Paste the job posting here - or just the company name", and the most
natural thing to put in it is the URL of the job. Every rule the resolver had
looked for a company NAME: a "Company:" line, a capitalised phrase after "at", a
short second line. A URL has none of those, so the page answered "No company name
found in that text" for a perfectly good posting, which reads as the tool being
broken rather than the paste being unreadable.

Three ways a link identifies an employer, in descending order of certainty:

  1. the warehouse already holds that exact posting, so the link IS the answer
  2. the applicant-tracking slug - boards.greenhouse.io/spacex/...
  3. the employer's own careers domain - klaviyo.com/careers/...

And one way it does not: a LinkedIn or Indeed link is an opaque job number that
names nobody, which is a different failure from an employer the dataset has not
collected, and has to say so differently.

No database: these cover the parsing. The matching is exercised against the real
warehouse in the service tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("storage", "ingestion", "service"):
    sys.path.insert(0, str(ROOT / sub))

from resolve import candidates, from_url, urls  # noqa: E402


class TestReadingTheEmployerOutOfALink:
    @pytest.mark.parametrize("url,expected", [
        ("https://boards.greenhouse.io/spacex/jobs/8692296002?gh_jid=8692296002",
         "spacex"),
        ("https://job-boards.greenhouse.io/fanaticscollectibles/jobs/4304421009",
         "fanaticscollectibles"),
        ("https://boards.greenhouse.io/andurilindustries/jobs/5032221007",
         "andurilindustries"),
        ("https://jobs.lever.co/acme/8a9f-uuid", "acme"),
        ("https://jobs.ashbyhq.com/socure/ac623889-7cbb", "socure"),
        ("https://apply.workable.com/someco/j/ABC123/", "someco"),
        ("https://oracle.wd1.myworkdayjobs.com/en-US/Careers/job/Austin/Eng",
         "oracle"),
        ("https://www.klaviyo.com/careers/jobs/7855881003", "klaviyo"),
    ])
    def test_the_slug_or_the_domain_names_the_employer(self, url, expected):
        assert expected in from_url(url)

    @pytest.mark.parametrize("url", [
        "https://www.linkedin.com/jobs/view/4123456789/",
        "https://www.indeed.com/viewjob?jk=abc123",
        "https://www.glassdoor.com/job-listing/xyz",
        "https://www.ziprecruiter.com/c/x/Job/y",
    ])
    def test_a_board_link_names_nobody(self, url):
        """
        And must not pretend otherwise. These carry a job number and a board's own
        domain; guessing "linkedin" or "indeed" as the employer would send the run
        off to look up a company that is not hiring anybody.
        """
        assert from_url(url) == []

    def test_a_board_in_the_host_is_not_a_company(self):
        """
        Testing only the first label of the host read "boards.greenhouse.io" as a
        company called "boards" and "jobs.lever.co" as one called "jobs", and both
        went to the database as real guesses.
        """
        for url in ("https://boards.greenhouse.io/spacex/jobs/1",
                    "https://jobs.lever.co/acme/x",
                    "https://jobs.ashbyhq.com/socure/x"):
            assert "boards" not in from_url(url)
            assert "jobs" not in from_url(url)

    def test_a_hyphenated_slug_keeps_its_words(self):
        """"capital-one" is "Capital One" with the spaces spelled differently."""
        assert "capital one" in from_url(
            "https://job-boards.greenhouse.io/capital-one/jobs/123")


class TestALinkIsNotACompanyName:
    def test_a_bare_url_is_not_offered_as_an_employer(self):
        """
        A link is short and has no spaces, so the "a short early line is probably
        the company" rule accepted the whole URL and handed it to the database as
        a company name.
        """
        assert candidates("https://www.linkedin.com/jobs/view/4123456789/") == []

    def test_a_url_inside_a_real_posting_does_not_displace_the_company(self):
        text = ("Data Engineer Intern\n"
                "Capital One\n"
                "Apply at https://job-boards.greenhouse.io/capital-one/jobs/9\n")
        found = candidates(text)
        assert "Capital One" in found
        assert not any(c.startswith("http") for c in found)


class TestFindingTheLinks:
    def test_urls_are_pulled_out_of_surrounding_text(self):
        text = "See (https://boards.greenhouse.io/spacex/jobs/1) for details."
        assert urls(text) == ["https://boards.greenhouse.io/spacex/jobs/1"]

    def test_text_with_no_link_yields_none(self):
        assert urls("Data Engineer Intern at Capital One") == []
