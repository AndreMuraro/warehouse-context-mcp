# /// script
# requires-python = ">=3.14"
# dependencies = ["cryptography>=45"]
# ///
"""Create an RSA key pair for Snowflake key-pair authentication. Works on Windows, no openssl.

    uv run scripts/snowflake_keypair.py [output_dir] [--encrypt]

Writes snowflake_key.p8 (PRIVATE: readable only by you, keep it out of git) and
snowflake_key.pub, then prints the ALTER USER statement to run once in Snowsight. Point
SNOWFLAKE_PRIVATE_KEY_PATH at the .p8 file. With --encrypt the key is protected by a
passphrase you type; set it in SNOWFLAKE_PRIVATE_KEY_PASSPHRASE.
"""

import getpass
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def write_private(path: Path, data: bytes) -> None:
    """Create the file owner-only from the start, so it is never briefly world-readable."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--encrypt"]
    out = Path(args[0]) if args else Path.home() / ".snowflake"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)

    encryption: serialization.KeySerializationEncryption = serialization.NoEncryption()
    if "--encrypt" in sys.argv:
        passphrase = getpass.getpass("Passphrase for the private key: ")
        if not passphrase or passphrase != getpass.getpass("Repeat it: "):
            sys.exit("Passphrases were empty or did not match.")
        encryption = serialization.BestAvailableEncryption(passphrase.encode())

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    write_private(out / "snowflake_key.p8", private)
    (out / "snowflake_key.pub").write_bytes(public)

    body = "".join(public.decode().strip().splitlines()[1:-1])
    print(f"Private key: {out / 'snowflake_key.p8'}  (set SNOWFLAKE_PRIVATE_KEY_PATH to this)")
    print("\nRun once in Snowsight, replacing the user name if yours differs:\n")
    print(f"alter user \"<your user name>\" set rsa_public_key = '{body}';")


if __name__ == "__main__":
    main()
