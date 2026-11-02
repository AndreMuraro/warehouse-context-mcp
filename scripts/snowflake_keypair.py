"""Create an RSA key pair for Snowflake key-pair authentication. Works on Windows, no openssl.

    python scripts/snowflake_keypair.py [output_dir]

Writes snowflake_key.p8 (PRIVATE: keep it out of git) and snowflake_key.pub, then prints the
ALTER USER statement to run once in a Snowsight worksheet. Point SNOWFLAKE_PRIVATE_KEY_PATH
in .env at the .p8 file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".snowflake")
    out.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (out / "snowflake_key.p8").write_bytes(private)
    (out / "snowflake_key.pub").write_bytes(public)
    body = "".join(public.decode().strip().splitlines()[1:-1])
    print(f"Private key: {out / 'snowflake_key.p8'}  (set SNOWFLAKE_PRIVATE_KEY_PATH to this)")
    print("\nRun once in Snowsight, replacing the user name if yours differs:\n")
    print(f"alter user \"<your user name>\" set rsa_public_key = '{body}';")


if __name__ == "__main__":
    main()
