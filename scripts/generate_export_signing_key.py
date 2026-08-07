from __future__ import annotations

import argparse
from pathlib import Path

from assuranceos.vault.signing import generate_ed25519_keypair


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an Ed25519 evidence-export signing key")
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--public", type=Path, required=True)
    args = parser.parse_args()
    generate_ed25519_keypair(args.private, args.public)
    print(f"created {args.private} and {args.public}")


if __name__ == "__main__":
    main()
