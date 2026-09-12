"""
One place that decides how to reach Snowflake.

Key-pair authentication, not a password. The account enforces multi-factor
authentication, and MFA is a property of interactive login - a driver presenting
a password gets rejected with "Multi-factor authentication is required for this
account", which no amount of retrying fixes. Key-pair is the supported route for
programmatic access and is what Snowflake recommends for it.

It is also simply better here. The password for this account is recoverable from
a chat transcript; the private key never left the machine that generated it and
is gitignored. Only the public half is registered on the user, with:

    ALTER USER <user> SET RSA_PUBLIC_KEY='<base64 body>';

Resolution order, so a fresh checkout fails with a sentence rather than a stack
trace:

    1. SNOWFLAKE_PRIVATE_KEY_PATH, or .snowflake_key.p8 beside the repo
    2. SNOWFLAKE_PASSWORD, which will fail on an MFA-enforced account but is
       kept for accounts that do not enforce it
"""

from __future__ import annotations

import os

DEFAULT_KEY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".snowflake_key.p8")


def _private_key_der() -> bytes | None:
    """The private key as DER, which is the only form the connector accepts."""
    path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", DEFAULT_KEY)
    if not os.path.exists(path):
        return None
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(
            fh.read(),
            password=passphrase.encode() if passphrase else None,
            backend=default_backend(),
        )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def describe() -> str:
    """Human-readable target, safe to log - never includes a credential."""
    account = os.getenv("SNOWFLAKE_ACCOUNT", "?")
    how = "key pair" if _private_key_der() else "password"
    return f"Snowflake {account} as {os.getenv('SNOWFLAKE_USER', '?')} ({how})"


def connect(**overrides):
    """Open a Snowflake connection. Keyword overrides win over the environment."""
    import snowflake.connector as sf

    account = os.getenv("SNOWFLAKE_ACCOUNT")
    user = os.getenv("SNOWFLAKE_USER")
    if not account or not user:
        raise SystemExit("SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER must be set")

    kwargs = {
        "account": account,
        "user": user,
        "database": os.getenv("SNOWFLAKE_DATABASE", "SIGNAL_DB"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        "login_timeout": 45,
    }
    if os.getenv("SNOWFLAKE_ROLE"):
        kwargs["role"] = os.environ["SNOWFLAKE_ROLE"]

    der = _private_key_der()
    if der:
        kwargs["private_key"] = der
    elif os.getenv("SNOWFLAKE_PASSWORD"):
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        raise SystemExit(
            f"No Snowflake credential. Expected a private key at {DEFAULT_KEY} "
            "(or SNOWFLAKE_PRIVATE_KEY_PATH), or SNOWFLAKE_PASSWORD."
        )

    kwargs.update(overrides)
    return sf.connect(**kwargs)
