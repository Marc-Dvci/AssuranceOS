from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.execution_security import generate_execution_envelope_keypair  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an Ed25519 control-plane key for signed execution envelopes."
    )
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    if args.private_key.exists() or args.public_key.exists():
        raise SystemExit("refusing to overwrite an existing execution-envelope key")
    generate_execution_envelope_keypair(args.private_key, args.public_key)
    print(f"private key: {args.private_key}")
    print(f"public key:  {args.public_key}")


if __name__ == "__main__":
    main()
