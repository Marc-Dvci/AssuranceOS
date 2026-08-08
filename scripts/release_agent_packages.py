from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.agent_release import build_release_document, sign_release_document

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create signed AssuranceOS agent releases")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--released-at", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--version", default="0.7.0")
    args = parser.parse_args()

    loaded = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(loaded, Ed25519PrivateKey):
        raise SystemExit("private key must be Ed25519")
    released_at = datetime.fromisoformat(args.released_at.replace("Z", "+00:00"))
    public_path = ROOT / "security/release-keys/agent-release-public.pem"
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(
        loaded.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    for agent_dir in sorted(path for path in (ROOT / "agents").iterdir() if path.is_dir()):
        manifest_path = agent_dir / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = args.version
        manifest["status"] = "released"
        release = dict(manifest.get("release") or {})
        release["prompt_hash"] = __import__("hashlib").sha256(
            (agent_dir / "system_prompt.md").read_bytes()
        ).hexdigest()
        release["signature"] = "release.signature.json"
        release["release_key_id"] = args.key_id
        manifest["release"] = release
        manifest_path.write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8", newline="\n"
        )

        document = build_release_document(
            agent_dir=agent_dir,
            agent_id=agent_dir.name,
            version=args.version,
            released_at=released_at,
        )
        signature = sign_release_document(document, private_key=loaded, key_id=args.key_id)
        (agent_dir / "release.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (agent_dir / "release.signature.json").write_text(
            json.dumps(signature, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print("Released 19 signed agent packages.")


if __name__ == "__main__":
    main()
