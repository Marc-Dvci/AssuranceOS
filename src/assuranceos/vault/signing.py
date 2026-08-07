from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature


class ManifestSigner(Protocol):
    key_id: str
    algorithm: str

    def sign(self, payload: bytes) -> bytes: ...
    def public_key_pem(self) -> bytes: ...


@dataclass(frozen=True)
class Ed25519ManifestSigner:
    private_key: Ed25519PrivateKey
    key_id: str
    algorithm: str = "Ed25519"

    @classmethod
    def from_pem(cls, path: Path, *, key_id: str) -> "Ed25519ManifestSigner":
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("export signing key must be an Ed25519 private key")
        return cls(private_key=key, key_id=key_id)

    def sign(self, payload: bytes) -> bytes:
        return self.private_key.sign(payload)

    def public_key_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )


def public_key_fingerprint(public_key_pem: bytes) -> str:
    key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("manifest public key must be Ed25519")
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def signature_document(*, signer: ManifestSigner, payload: bytes) -> dict[str, str]:
    public_pem = signer.public_key_pem()
    return {
        "algorithm": signer.algorithm,
        "key_id": signer.key_id,
        "public_key_sha256": public_key_fingerprint(public_pem),
        "signature_base64": base64.b64encode(signer.sign(payload)).decode("ascii"),
    }


def verify_signature(
    *, payload: bytes, signature: dict[str, str], public_key_pem: bytes
) -> None:
    if signature.get("algorithm") != "Ed25519":
        raise ValueError("unsupported manifest signature algorithm")
    if signature.get("public_key_sha256") != public_key_fingerprint(public_key_pem):
        raise ValueError("manifest public key fingerprint mismatch")
    try:
        raw_signature = base64.b64decode(signature["signature_base64"], validate=True)
    except Exception as exc:
        raise ValueError("manifest signature is not valid base64") from exc
    key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("manifest public key must be Ed25519")
    try:
        key.verify(raw_signature, payload)
    except InvalidSignature as exc:
        raise ValueError("manifest signature verification failed") from exc


def generate_ed25519_keypair(private_path: Path, public_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
