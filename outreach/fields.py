"""
What the visitor said they do, turned into the kinds of tools they would care about.

The public page asks "What you do" and takes free text - "Ai Engineer", "CS
student", "analyst moving into data engineering", "mechanical engineering student".
That answer used to appear only as prose in the second paragraph of the email. It
now also decides WHICH fact about the company leads it, because a machine-learning
tool is an interesting thing to open with for one of those people and noise for
another.

Deliberately a keyword map and nothing cleverer. The alternative - asking a model
to classify the field - would put a model call in front of every draft, make the
opener non-deterministic, and fail in a way nobody could debug. A missed keyword
here costs the visitor a *less tailored* opening line, not a wrong one: no match
means no preference, and the rarest tool at the company leads instead. That is a
good failure, and it is the common one, because the list below cannot cover every
job title a person might type.

The categories are dim_technology.category, which is a tooling taxonomy rather than
a discipline one - so this maps a discipline onto the tools it touches, not onto
some field the data does not have.
"""

from __future__ import annotations

# Ordered: the first phrase found in what they typed wins. "machine learning
# engineer" must be tested before "engineer", and "data engineer" before "data",
# so longer and more specific phrases come first within each group.
FIELD_CATEGORIES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("machine learning", "deep learning", "ml engineer", "ai engineer",
      "data scien", " ai", "ai ", "artificial intelligence", "nlp",
      "computer vision", "llm"),
     frozenset({"ml"})),

    (("data engineer", "data engineering", "etl", "elt", "pipeline",
      "analytics engineer", "data platform"),
     frozenset({"orchestration", "transform", "ingestion", "warehouse",
                "streaming"})),

    (("business intelligence", "data analyst", "analytics", "analyst", " bi",
      "bi ", "reporting"),
     frozenset({"bi", "warehouse"})),

    (("devops", "site reliability", "sre", "infrastructure", "infra",
      "platform engineer", "cloud engineer", "cloud"),
     frozenset({"infra", "cloud"})),

    (("software engineer", "software developer", "computer science",
      "cs student", "swe", "backend", "back end", "full stack", "fullstack",
      "developer", "programmer"),
     frozenset({"language", "infra", "database"})),
)

# Everything, which is what "no preference" means to the caller: no category is
# ranked above another and the rarest tool wins outright.
NO_PREFERENCE: frozenset[str] = frozenset()


def categories_for(what_they_do: str | None) -> frozenset[str]:
    """
    Tooling categories a person in this field would recognise, or none.

    Matching is on substrings of the lowercased text, so "Senior Data Engineer"
    and "aspiring data engineer" both land on the same answer. Returns an empty
    set for anything unrecognised - a nurse, a mechanical engineer, a blank box -
    and the caller treats that as "no preference" rather than as an error.
    """
    text = f" {(what_they_do or '').lower().strip()} "
    if not text.strip():
        return NO_PREFERENCE
    for phrases, categories in FIELD_CATEGORIES:
        if any(phrase in text for phrase in phrases):
            return categories
    return NO_PREFERENCE
