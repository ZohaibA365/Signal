"""
What a visitor is allowed to tell us about themselves.

Separate from app.py, and not for tidiness. clean_sender is the most
safety-critical function in this service - it decides whether a message is written
for the visitor or for the dataset's owner, and returning None from it once meant
every visitor who left the boxes empty was handed the owner's email. That is
exactly the function that must be covered by the test suite CI runs.

app.py imports FastAPI at module scope, and CI installs only requirements-dev.txt,
which does not include it. So a test that reached this logic through app.py could
not run in CI at all: it failed there while passing on a laptop where the web
dependencies happen to be installed. Pure functions with no web imports can be
tested anywhere, which is the whole reason this file exists.
"""

from __future__ import annotations

# What a visitor may tell us about themselves. Short, because these land inside a
# message somebody may send: a long free-text field here is a way to write most of
# somebody else's email through this service.
SENDER_FIELDS = {"name": 60, "program": 80, "school": 80, "term": 40, "role": 60}


def clean_sender(raw: dict | None) -> dict:
    """
    The visitor's own details, trimmed and bounded. Always a dict, never None.

    Stripped of newlines and truncated per field. Everything here is interpolated
    into a draft, and the verifier checks the draft's FIGURES rather than its
    prose, so the length limits are the control on what can be injected through it.

    It used to return None when nothing was filled in, and that single line was
    the worst bug in this service. None flowed into DraftingContext, which read it
    as "no sender given, so use the configured profile", and the profile is the
    owner's. A visitor who left the optional boxes empty - or clicked a preset,
    which sends no sender at all - was shown the owner's email, his university and
    a link to his project, while the rail that exists to catch exactly that was
    switched off by the same missing value. Nothing raised, nothing logged.

    An empty form is now an empty dict: a person who told us nothing about
    themselves, which is a perfectly ordinary thing to be.
    """
    out: dict = {}
    if isinstance(raw, dict):
        for field, cap in SENDER_FIELDS.items():
            value = raw.get(field)
            if isinstance(value, str) and value.strip():
                out[field] = " ".join(value.split())[:cap]
    # The templates read these; missing ones render as empty rather than breaking.
    out.setdefault("months", 4)
    return out
