"""
Tests for rebuilding the Snowflake private key from a pasted secret.

The first parity run failed here and nowhere else: the secret was set, the key
did not parse, and nothing downstream ran. A PEM is a multi-line value being
typed into a one-line form, so the mangled shapes are predictable - and each one
is tested against a real generated key, because "it parses" is the only
assertion that means anything about a key.
"""

import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from snowflake_key import BEGIN, END, normalise


@pytest.fixture(scope="module")
def pem() -> str:
    """A real PKCS#8 PEM. 1024 bits: this tests formatting, not cryptography."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def parses(text: str):
    return serialization.load_pem_private_key(text.encode(), password=None)


class TestShapesASecretArrivesIn:
    def test_a_correct_pem_is_unchanged_in_substance(self, pem):
        assert parses(normalise(pem)).key_size == 1024

    def test_newlines_that_became_backslash_n(self, pem):
        assert parses(normalise(pem.replace("\n", "\\n"))).key_size == 1024

    def test_flattened_onto_one_line_with_spaces(self, pem):
        """What a paste into a single-line form actually produces."""
        assert parses(normalise(pem.replace("\n", " "))).key_size == 1024

    def test_flattened_with_nothing_between_the_parts(self, pem):
        assert parses(normalise(pem.replace("\n", ""))).key_size == 1024

    def test_body_only_with_no_header_or_footer(self, pem):
        body = pem.replace(BEGIN, "").replace(END, "").strip()
        assert parses(normalise(body)).key_size == 1024

    def test_surrounding_whitespace_and_a_trailing_newline(self, pem):
        assert parses(normalise("\n  " + pem + "  \n\n")).key_size == 1024

    def test_every_shape_yields_the_same_key(self, pem):
        shapes = [pem, pem.replace("\n", "\\n"), pem.replace("\n", " "),
                  pem.replace("\n", "")]
        assert len({normalise(s) for s in shapes}) == 1


class TestRefusals:
    def test_empty_is_refused(self):
        with pytest.raises(SystemExit):
            normalise("   \n  ")

    def test_non_base64_is_refused(self):
        """Usually the wrong secret pasted, e.g. the account name or a password."""
        with pytest.raises(SystemExit):
            normalise("Zohaib365!!!!!")

    def test_an_encrypted_key_is_refused_rather_than_half_accepted(self):
        encrypted = ("-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n"
                     "-----END ENCRYPTED PRIVATE KEY-----")
        with pytest.raises(SystemExit, match="unencrypted"):
            normalise(encrypted)

    def test_a_public_key_is_refused(self, pem):
        """
        The easy mistake to make: the public half is what gets registered on the
        Snowflake user, so both files are open at the same moment. It is rejected
        on its own header, which never reaches the base64 check.
        """
        public = rsa.generate_private_key(
            public_exponent=65537, key_size=1024).public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        with pytest.raises(SystemExit):
            normalise(public)


class TestLineLength:
    def test_body_is_wrapped_at_64_characters(self, pem):
        lines = normalise(pem.replace("\n", "")).splitlines()
        body = lines[1:-1]
        assert all(len(line) == 64 for line in body[:-1])
        assert len(body[-1]) <= 64

    def test_the_header_and_footer_are_on_their_own_lines(self, pem):
        lines = normalise(pem.replace("\n", " ")).splitlines()
        assert lines[0] == BEGIN
        assert lines[-1] == END
