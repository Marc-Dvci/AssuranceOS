from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.audit_pack_release import build_release_document, sign_release_document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a signed immutable Audit Pack release")
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--released-at", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pack = yaml.safe_load((args.pack_dir / "pack.yaml").read_text(encoding="utf-8"))
    key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("release private key must be Ed25519")
    released_at = datetime.fromisoformat(args.released_at.replace("Z", "+00:00"))
    document = build_release_document(
        pack_dir=args.pack_dir,
        pack_id=str(pack["pack_id"]),
        version=str(pack["version"]),
        released_at=released_at,
    )
    signature = sign_release_document(document, private_key=key, key_id=args.key_id)
    (args.pack_dir / "release.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.pack_dir / "release.signature.json").write_text(
        json.dumps(signature, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Released {document['pack_id']}@{document['version']} ({document['package_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
