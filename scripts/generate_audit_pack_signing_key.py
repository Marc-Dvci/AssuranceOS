"""Generate the Ed25519 key that signs Audit Pack releases.

Kept separate from the agent-release key on purpose. Agent packages and Audit
Packs are different artefact classes with different authors and different review
paths, and a single key that signs both means compromising either review
compromises the other. In a deployed environment these become two KMS keys with
different IAM bindings; here they are two files, only one half of each committed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an Ed25519 Audit Pack release key")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    if args.private_key.exists() or args.public_key.exists():
        raise SystemExit("refusing to overwrite an existing signing key")
    private_key = Ed25519PrivateKey.generate()
    args.private_key.parent.mkdir(parents=True, exist_ok=True)
    args.public_key.parent.mkdir(parents=True, exist_ok=True)
    args.private_key.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    args.private_key.chmod(0o600)
    args.public_key.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print(f"Wrote private key to {args.private_key}")
    print(f"Wrote public key to {args.public_key}")


if __name__ == "__main__":
    main()
