"""
Every Anthropic client is built from a stripped key.

This is a text check rather than a behavioural one because the failure it prevents
is invisible at every level above the wire. A key pasted into a hosting dashboard
can arrive with a trailing newline; that newline makes an invalid HTTP header; the
SDK reports it as `APIConnectionError: Connection error.` So the message says the
network is unreachable, while the network is fine, the key is valid, and the
database it talks to on the same egress is answering normally. The public service
spent two hours telling visitors "that run could not be completed" on one
whitespace character.

Nothing at runtime can catch this - by the time the exception exists the cause is
already gone from it - so the rule is enforced where it can be: a client is
constructed with an explicit, stripped api_key, or this fails.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Every module that builds a client, and therefore every place the rule applies.
SOURCES = [
    "agent/agent.py",
    "service/app.py",
    "ai_layer/enrich.py",
    "eval/run_eval.py",
]


def constructions(path: Path) -> list[ast.Call]:
    """Every `anthropic.Anthropic(...)` call in the file."""
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None)
        if name == "Anthropic":
            found.append(node)
    return found


@pytest.mark.parametrize("relative", SOURCES)
def test_client_is_built_with_a_stripped_key(relative):
    path = ROOT / relative
    assert path.exists(), f"{relative} has moved; update this test's list"

    calls = constructions(path)
    assert calls, f"{relative} no longer builds a client; remove it from this list"

    for call in calls:
        keyword = next((k for k in call.keywords if k.arg == "api_key"), None)
        assert keyword is not None, (
            f"{relative}:{call.lineno} builds an Anthropic client without an "
            f"explicit api_key, so it reads the environment variable verbatim - "
            f"including any whitespace that came with it when it was pasted.")

        # The value has to be an actual .strip() call, not merely a name that
        # happens to read like one.
        value = keyword.value
        assert (isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "strip"), (
            f"{relative}:{call.lineno} passes an api_key that is not stripped.")


def test_whitespace_in_a_key_is_what_breaks_the_header():
    """
    The mechanism itself, so the rule above cannot be dismissed as superstition.

    The rejection happens while the request is being built, before anything is
    sent. That is precisely why it surfaces as a connection error: there is no
    response to read a status code from, so the SDK has nothing to report except
    that the call did not reach the server.
    """
    requests = pytest.importorskip("requests")
    from requests.models import PreparedRequest  # noqa: PLC0415

    bad = "sk-ant-valid-looking-key\n"

    with pytest.raises(requests.exceptions.InvalidHeader):
        PreparedRequest().prepare(method="GET", url="https://example.com",
                                  headers={"x-api-key": bad})

    # And the stripped form prepares cleanly, so .strip() is a fix, not a mask.
    prepared = PreparedRequest()
    prepared.prepare(method="GET", url="https://example.com",
                     headers={"x-api-key": bad.strip()})
    assert prepared.headers["x-api-key"] == "sk-ant-valid-looking-key"
