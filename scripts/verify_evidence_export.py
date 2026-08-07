from __future__ import annotations

import argparse
import json
from pathlib import Path

from assuranceos.vault.export import verify_export_package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify an AssuranceOS evidence export, including its Ed25519 signature."
    )
    parser.add_argument("package", type=Path, help="Evidence export ZIP to verify")
    parser.add_argument(
        "--public-key",
        type=Path,
        required=True,
        help="Trusted Ed25519 public key PEM",
    )
    parser.add_argument(
        "--key-id",
        required=True,
        help="Expected signing key identifier embedded in the package",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verification = verify_export_package(
        args.package,
        trusted_public_keys={args.key_id: args.public_key.read_bytes()},
    )
    print(json.dumps(verification.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0 if verification.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
