"""
Applicant-tracking-system adapters: one module per system, one shape out.

Each adapter exposes `NAME`, `probe(**coords) -> int | None` and
`fetch(**coords) -> list[dict]`, so the discovery sweep and the ingester can
treat every system identically.

Coordinates differ by system and that difference is the point. The startup
boards take a single guessable `slug`; Workday takes `tenant`, `host` and
`site`, none of which can be guessed. Adapters are therefore ordered cheapest
first - the three slug-only boards are one request each, so they are tried
before anything that needs a registry entry.
"""
from __future__ import annotations

from . import (
    amazon,
    ashby,
    greenhouse,
    lever,
    oraclecloud,
    rippling,
    smartrecruiters,
    workable,
    workday,
)

ADAPTERS = {m.NAME: m for m in
            (greenhouse, lever, ashby, smartrecruiters, workable, workday,
             amazon, oraclecloud, rippling)}

# Systems whose coordinates are a single slug, so a sweep can guess them.
GUESSABLE = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable",
             "rippling")


def fetch(ats: str, **coords) -> list[dict]:
    """Fetch every posting for one registry entry."""
    adapter = ADAPTERS.get(ats)
    if adapter is None:
        raise ValueError(f"unknown ATS {ats!r}; known: {', '.join(ADAPTERS)}")
    return adapter.fetch(**coords)


def probe(ats: str, **coords) -> int | None:
    adapter = ADAPTERS.get(ats)
    return adapter.probe(**coords) if adapter else None
