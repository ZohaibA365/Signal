"""
Tests for rebuilding the Snowflake private key from a pasted secret.

The first parity run failed here and nowhere else: the secret was set, the key
did not parse, and nothing downstream ran. A PEM is a multi-line value being
typed into a one-line web form, so the mangled shapes are predictable.

This file tests the reconstruction as pure string work and imports nothing
optional. The companion file checks that the result is a real key, which needs
cryptography - a package CI deliberately does not install, because it belongs to
the Snowflake extras. Keeping the two apart is the lesson from doing it wrong
twice: a module-level import of an optional package fails collection and takes
the whole suite down, and a module-level importorskip passes while checking
nothing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from snowflake_key import BEGIN, END, normalise

# Not a key, and deliberately not one: these tests reformat a PEM and never
# open it, so a real private key here would only give secret scanners
# something to find. The companion file generates a genuine key at run time
# for the tests that actually parse one.
PEM = """-----BEGIN PRIVATE KEY-----
Zm9ybWF0dGluZy1vbmx5LW5vdC1hLWtleQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
Zm9ybWF0dGluZy1vbmx5LW5vdC1hLWtleQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
Zm9ybWF0dGluZy1vbmx5LW5vdC1hLWtleQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
QUJD
-----END PRIVATE KEY-----
"""


class TestShapesASecretArrivesIn:
    def test_a_correct_pem_is_unchanged(self):
        assert normalise(PEM) == PEM

    def test_newlines_that_became_backslash_n(self):
        assert normalise(PEM.replace("\n", "\\n")) == PEM

    def test_flattened_onto_one_line_with_spaces(self):
        """What a paste into a single-line form actually produces."""
        assert normalise(PEM.replace("\n", " ")) == PEM

    def test_flattened_with_nothing_between_the_parts(self):
        assert normalise(PEM.replace("\n", "")) == PEM

    def test_body_only_with_no_header_or_footer(self):
        body = PEM.replace(BEGIN, "").replace(END, "").strip()
        assert normalise(body) == PEM

    def test_surrounding_whitespace(self):
        assert normalise("\n  " + PEM + "  \n\n") == PEM


class TestRefusals:
    def test_empty_is_refused(self):
        with pytest.raises(SystemExit):
            normalise("   \n  ")

    def test_non_base64_is_refused(self):
        """Usually the wrong secret pasted - an account name, or a password."""
        with pytest.raises(SystemExit):
            normalise("Zohaib365!!!!!")

    def test_an_encrypted_key_is_refused_rather_than_half_accepted(self):
        encrypted = ("-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n"
                     "-----END ENCRYPTED PRIVATE KEY-----")
        with pytest.raises(SystemExit, match="unencrypted"):
            normalise(encrypted)

    def test_a_public_key_is_refused(self):
        """
        The easy mistake: the public half is what gets registered on the
        Snowflake user, so both files are open at the same moment. Its header is
        not the one this strips, so the leftover dashes fail the base64 check.
        """
        public = ("-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A\n"
                  "-----END PUBLIC KEY-----")
        with pytest.raises(SystemExit):
            normalise(public)


class TestLineLength:
    def test_body_is_wrapped_at_64_characters(self):
        lines = normalise(PEM.replace("\n", "")).splitlines()
        body = lines[1:-1]
        assert all(len(line) == 64 for line in body[:-1])
        assert len(body[-1]) <= 64

    def test_the_header_and_footer_are_on_their_own_lines(self):
        lines = normalise(PEM.replace("\n", " ")).splitlines()
        assert lines[0] == BEGIN
        assert lines[-1] == END
