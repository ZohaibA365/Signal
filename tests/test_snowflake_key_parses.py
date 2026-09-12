"""
Does the rebuilt key actually parse as a private key?

Separate from tests/test_snowflake_key.py because this needs cryptography, which
lives in requirements-snowflake.txt and is not installed in CI. Skipped there,
run wherever Snowflake work happens - and the skip is at the top of this file
only, so the formatting tests next door always run.
"""

import sys
from pathlib import Path

import pytest

serialization = pytest.importorskip(
    "cryptography.hazmat.primitives.serialization",
    reason="cryptography ships with the Snowflake extras, not with CI",
)
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# After the skips above on purpose: there is nothing to test if the key
# library is absent, and ruff cannot see that the preamble is deliberate.
from snowflake_key import BEGIN, END, normalise  # noqa: E402


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


class TestEveryShapeYieldsAUsableKey:
    def test_a_correct_pem(self, pem):
        assert parses(normalise(pem)).key_size == 1024

    def test_newlines_that_became_backslash_n(self, pem):
        assert parses(normalise(pem.replace("\n", "\\n"))).key_size == 1024

    def test_flattened_onto_one_line(self, pem):
        assert parses(normalise(pem.replace("\n", " "))).key_size == 1024

    def test_flattened_with_nothing_between_the_parts(self, pem):
        assert parses(normalise(pem.replace("\n", ""))).key_size == 1024

    def test_body_only(self, pem):
        body = pem.replace(BEGIN, "").replace(END, "").strip()
        assert parses(normalise(body)).key_size == 1024

    def test_every_shape_rebuilds_the_same_key(self, pem):
        shapes = [pem, pem.replace("\n", "\\n"), pem.replace("\n", " "),
                  pem.replace("\n", "")]
        assert len({normalise(s) for s in shapes}) == 1
