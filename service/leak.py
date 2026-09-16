"""
What must never reach a visitor: the dataset owner's own text.

Separate from app.py so the suite CI runs can cover it. app.py imports FastAPI,
which CI does not install, so a test reaching this through app.py fails there and
nowhere else - the same trap clean_sender was moved out of.

This is the last check before a draft is streamed, and it is deliberately dumb. Four
separate fixes upstream - the template, the model prompt, the verifier, the recorded
example - were each believed complete, and what they had in common was that nothing
inspected the finished message on its way out. This does, whichever path wrote it.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _sub in ("outreach", "agent"):
    _path = os.path.join(os.path.dirname(_HERE), _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

class OwnerTextLeaked(RuntimeError):
    """A draft about to be shown to a visitor carried the owner's identity."""


# Everything that would mark a message as the dataset owner's rather than the
# sender's. Assembled from the profile itself rather than typed out again, so it
# cannot fall out of date with it.
def _owner_markers() -> tuple[str, ...]:
    """
    What actually indicates the owner's text leaked into a visitor's message.

    The first version also matched the owner's school and degree, and that was
    wrong: "University of Waterloo" and "Management Engineering" describe tens of
    thousands of people, most obviously the classmates most likely to try this
    page. A Waterloo Management Engineering student typing their own real details
    had their draft refused outright, which is the opposite of the bug this rail
    exists to prevent - it stopped the honest case and caught the dishonest one no
    better.

    What remains is what only the owner's drafts contain: the address of the
    dataset, and the claim to have built it. The leak this rail was written for -
    an email saying "I built a dataset tracking hiring trends" with a link to it -
    is still caught by both, twice over.
    """
    from compose import SITE_URL  # noqa: PLC0415
    from verify import AUTHORSHIP_CLAIMS  # noqa: PLC0415

    return tuple(m.lower() for m in (SITE_URL, *AUTHORSHIP_CLAIMS) if m)


OWNER_MARKERS = _owner_markers()


def owner_traces(text: str) -> list[str]:
    """
    Anything in this text that belongs to the owner and not to the visitor.

    A backstop, deliberately dumb and deliberately last. Four separate fixes have
    been made upstream of here - the template, the model prompt, the verifier, the
    recorded example - and each of them was believed to be complete. What they had
    in common was that nothing checked the finished message on its way out the
    door. This does, and it runs no matter which path wrote the text.
    """
    lowered = (text or "").lower()
    return [m for m in OWNER_MARKERS if m in lowered]


