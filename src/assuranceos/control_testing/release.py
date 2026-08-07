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

_EXCLUDES = {"release.json", "release.signature.json"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def package_files(package_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(package_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in package_dir.rglob("*") if item.is_file())
        if path.relative_to(package_dir).as_posix() not in _EXCLUDES
        and "__pycache__" not in path.parts
    }


def package_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(files)).hexdigest()


def build_release_document(
    *, package_dir: Path, test_id: str, version: str, released_at: datetime
) -> dict[str, Any]:
    files = package_files(package_dir)
    return {
        "schema": "assurance.control_test_release.v1",
        "test_id": test_id,
        "version": version,
        "released_at": released_at.isoformat(),
        "files": files,
        "package_sha256": package_digest(files),
    }


def sign_release_document(
    document: dict[str, Any], *, private_key: Ed25519PrivateKey, key_id: str
) -> dict[str, str]:
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "schema": "assurance.control_test_release_signature.v1",
        "algorithm": "Ed25519",
        "key_id": key_id,
        "public_key_sha256": hashlib.sha256(public_raw).hexdigest(),
        "signature_base64": base64.b64encode(private_key.sign(canonical_json(document))).decode(),
    }


def verify_control_test_release(package_dir: Path, public_key_pem: bytes) -> dict[str, Any]:
    document_path = package_dir / "release.json"
    signature_path = package_dir / "release.signature.json"
    if not document_path.exists() or not signature_path.exists():
        raise ValueError(f"control test package {package_dir.name!r} is not release-signed")
    document = json.loads(document_path.read_text(encoding="utf-8"))
    signature = json.loads(signature_path.read_text(encoding="utf-8"))
    if document.get("schema") != "assurance.control_test_release.v1":
        raise ValueError("unsupported control-test release schema")
    if signature.get("schema") != "assurance.control_test_release_signature.v1":
        raise ValueError("unsupported control-test signature schema")
    files = package_files(package_dir)
    if document.get("files") != files or document.get("package_sha256") != package_digest(files):
        raise ValueError("control-test release file manifest does not match")
    key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("control-test public key must be Ed25519")
    public_raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if signature.get("public_key_sha256") != hashlib.sha256(public_raw).hexdigest():
        raise ValueError("control-test public-key fingerprint does not match")
    try:
        key.verify(
            base64.b64decode(signature["signature_base64"], validate=True),
            canonical_json(document),
        )
    except (KeyError, ValueError, InvalidSignature) as exc:
        raise ValueError("control-test release signature is invalid") from exc
    return document
