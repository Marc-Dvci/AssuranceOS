from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_RELEASE_EXCLUDES = {"release.json", "release.signature.json"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def package_files(pack_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(item for item in pack_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(pack_dir).as_posix()
        if relative in _RELEASE_EXCLUDES or "__pycache__" in Path(relative).parts:
            continue
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def package_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(files)).hexdigest()


def build_release_document(
    *, pack_dir: Path, pack_id: str, version: str, released_at: datetime
) -> dict[str, Any]:
    files = package_files(pack_dir)
    return {
        "schema": "assurance.audit_pack_release.v1",
        "pack_id": pack_id,
        "version": version,
        "released_at": released_at.isoformat(),
        "files": files,
        "package_sha256": package_digest(files),
    }


def sign_release_document(
    document: dict[str, Any], *, private_key: Ed25519PrivateKey, key_id: str
) -> dict[str, str]:
    public_key = private_key.public_key()
    public_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "schema": "assurance.audit_pack_release_signature.v1",
        "algorithm": "Ed25519",
        "key_id": key_id,
        "public_key_sha256": hashlib.sha256(public_raw).hexdigest(),
        "signature_base64": base64.b64encode(private_key.sign(canonical_json(document))).decode(
            "ascii"
        ),
    }


def verify_audit_pack_release(pack_dir: Path, public_key_pem: bytes) -> dict[str, Any]:
    document_path = pack_dir / "release.json"
    signature_path = pack_dir / "release.signature.json"
    if not document_path.exists() or not signature_path.exists():
        raise ValueError(f"audit pack {pack_dir.name!r} is not release-signed")
    document = json.loads(document_path.read_text(encoding="utf-8"))
    signature = json.loads(signature_path.read_text(encoding="utf-8"))
    if document.get("schema") != "assurance.audit_pack_release.v1":
        raise ValueError("unsupported Audit Pack release schema")
    if signature.get("schema") != "assurance.audit_pack_release_signature.v1":
        raise ValueError("unsupported Audit Pack release signature schema")
    if signature.get("algorithm") != "Ed25519":
        raise ValueError("unsupported Audit Pack signature algorithm")
    expected_files = package_files(pack_dir)
    if document.get("files") != expected_files:
        raise ValueError(f"audit pack {pack_dir.name!r} file manifest does not match")
    if document.get("package_sha256") != package_digest(expected_files):
        raise ValueError(f"audit pack {pack_dir.name!r} digest does not match")
    key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Audit Pack release public key must be Ed25519")
    public_raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if signature.get("public_key_sha256") != hashlib.sha256(public_raw).hexdigest():
        raise ValueError("Audit Pack release public-key fingerprint does not match")
    try:
        raw_signature = base64.b64decode(signature["signature_base64"], validate=True)
        key.verify(raw_signature, canonical_json(document))
    except (KeyError, ValueError, InvalidSignature) as exc:
        raise ValueError(f"audit pack {pack_dir.name!r} signature is invalid") from exc
    return document
