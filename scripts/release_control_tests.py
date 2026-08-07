from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.control_testing.release import build_release_document, sign_release_document

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign deterministic control-test releases")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", default="assuranceos-control-tests-2026-08")
    args = parser.parse_args()
    private_key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit("private key must be Ed25519")
    for manifest_path in sorted((ROOT / "tests-library").rglob("manifest.yaml")):
        package_dir = manifest_path.parent
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "assurance.control_test_manifest.v1":
            continue
        document = build_release_document(
            package_dir=package_dir,
            test_id=manifest["test_id"],
            version=manifest["version"],
            released_at=datetime.now(timezone.utc),
        )
        signature = sign_release_document(document, private_key=private_key, key_id=args.key_id)
        (package_dir / "release.json").write_text(json.dumps(document, indent=2) + "\n")
        (package_dir / "release.signature.json").write_text(json.dumps(signature, indent=2) + "\n")
        print(f"released {manifest['test_id']}@{manifest['version']}")


if __name__ == "__main__":
    main()
