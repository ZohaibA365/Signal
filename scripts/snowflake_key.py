"""
Write the Snowflake private key to a file, whatever shape it arrived in.

Key-pair authentication exists here because the Snowflake account enforces MFA,
which a password-presenting driver cannot satisfy. So the key has to travel as a
GitHub secret, and a PEM is a multi-line value being pasted into a single-line
web form by hand. The first attempt failed exactly there: the secret was present,
the key did not parse, and the run died before reaching Snowflake at all.

That is not worth a second manual paste, because the information is all still
there - a PEM is a header, base64, and a footer, and only the line breaks go
missing. This reconstructs them. It accepts:

  - a correct PEM, which it passes through unchanged
  - a PEM whose newlines became literal backslash-n
  - a PEM flattened onto one line, with or without spaces between the parts
  - bare base64 with no header or footer at all

and refuses anything it cannot make into a key, because writing a file that only
fails later, inside a driver, would be worse than failing here.

Usage:
    SNOWFLAKE_PRIVATE_KEY="$SECRET" python scripts/snowflake_key.py .snowflake_key.p8
"""

from __future__ import annotations

import os
import re
import sys

from cryptography.hazmat.primitives import serialization

BEGIN = "-----BEGIN PRIVATE KEY-----"
END = "-----END PRIVATE KEY-----"

# Snowflake's own docs show both forms, and the encrypted one needs a passphrase
# this deliberately does not carry - the key registered on the account is
# unencrypted, and silently accepting an encrypted one would fail in the driver.
ENCRYPTED = "-----BEGIN ENCRYPTED PRIVATE KEY-----"


def normalise(raw: str) -> str:
    """A parseable PKCS#8 PEM, rebuilt from however the secret survived pasting."""
    text = raw.strip()
    if ENCRYPTED in text:
        raise SystemExit(
            "the key is passphrase-encrypted; register an unencrypted PKCS#8 key "
            "instead, since nothing here holds a passphrase to open it"
        )

    # A secret pasted through a shell or a JSON field often arrives with its
    # newlines as two characters rather than one.
    text = text.replace("\\n", "\n")

    # Everything that is not base64 goes, which covers both the header and
    # footer and any spaces or newlines a flattened paste left between them.
    body = text.replace(BEGIN, "").replace(END, "")
    body = re.sub(r"\s+", "", body)
    if not body:
        raise SystemExit("SNOWFLAKE_PRIVATE_KEY holds no key material")
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", body):
        raise SystemExit("SNOWFLAKE_PRIVATE_KEY is not base64 - is it the right secret?")

    # 64 characters per line is what every PEM writer emits and what every reader
    # expects; the length is the one part of the format that cannot be recovered
    # from the value itself, so it is simply restored.
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "\n".join([BEGIN, *lines, END]) + "\n"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ".snowflake_key.p8"
    raw = os.getenv("SNOWFLAKE_PRIVATE_KEY")
    if not raw:
        raise SystemExit("SNOWFLAKE_PRIVATE_KEY is not set")

    pem = normalise(raw)
    # Written before parsing so the mode is never briefly wider than this.
    old = os.umask(0o077)
    try:
        with open(path, "w") as fh:
            fh.write(pem)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)

    # Parsed from the file rather than the string, so what is checked is exactly
    # what the driver will later open.
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    print(f"wrote {path}: {type(key).__name__}, {key.key_size} bits")


if __name__ == "__main__":
    main()
