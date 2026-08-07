from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping, Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from .models import ExecutionEnvelope


class SignedExecutionEnvelope(BaseModel):
    """Cryptographically authenticated authority for one bounded agent invocation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["assurance.signed_execution_envelope.v1"] = Field(
        default="assurance.signed_execution_envelope.v1",
        alias="schema",
        serialization_alias="schema",
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(min_length=1, max_length=128)
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=16, max_length=128)
    envelope: ExecutionEnvelope
    signature_base64: str = Field(min_length=1)

    def signing_document(self) -> dict[str, object]:
        return {
            "schema": self.schema_name,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "public_key_sha256": self.public_key_sha256,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce": self.nonce,
            "envelope": self.envelope.model_dump(mode="json"),
        }


class ExecutionEnvelopeSignerProtocol(Protocol):
    key_id: str

    def issue(
        self,
        envelope: ExecutionEnvelope,
        *,
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> SignedExecutionEnvelope: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


def _public_key_fingerprint(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Ed25519ExecutionEnvelopeSigner:
    private_key: Ed25519PrivateKey
    key_id: str

    @classmethod
    def from_pem(cls, path: Path, *, key_id: str) -> "Ed25519ExecutionEnvelopeSigner":
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("execution-envelope signing key must be Ed25519")
        return cls(private_key=key, key_id=key_id)

    def issue(
        self,
        envelope: ExecutionEnvelope,
        *,
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> SignedExecutionEnvelope:
        if ttl <= timedelta(0):
            raise ValueError("execution-envelope TTL must be positive")
        issued_at = _utc(now or datetime.now(timezone.utc), field_name="issued_at")
        expires_at = issued_at + ttl
        if envelope.deadline is not None:
            deadline = _utc(envelope.deadline, field_name="envelope.deadline")
            if deadline <= issued_at:
                raise ValueError("execution-envelope deadline has already passed")
            expires_at = min(expires_at, deadline)

        public_key = self.private_key.public_key()
        unsigned = SignedExecutionEnvelope(
            key_id=self.key_id,
            public_key_sha256=_public_key_fingerprint(public_key),
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce or f"env_{uuid4().hex}",
            envelope=envelope,
            signature_base64="pending",
        )
        signature = self.private_key.sign(_canonical_json(unsigned.signing_document()))
        return unsigned.model_copy(
            update={"signature_base64": base64.b64encode(signature).decode("ascii")}
        )


@dataclass(frozen=True)
class ExecutionEnvelopeVerifier:
    """Verify issuer identity, signature, bounded lifetime, and task deadline."""

    trusted_public_keys: Mapping[str, bytes]
    maximum_ttl: timedelta = timedelta(hours=24)
    clock_skew: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        if not self.trusted_public_keys:
            raise ValueError("at least one execution-envelope trust key is required")
        if self.maximum_ttl <= timedelta(0):
            raise ValueError("maximum execution-envelope TTL must be positive")
        if self.clock_skew < timedelta(0):
            raise ValueError("execution-envelope clock skew cannot be negative")

    @classmethod
    def from_pem_files(
        cls,
        keys: Mapping[str, Path],
        *,
        maximum_ttl: timedelta = timedelta(hours=24),
        clock_skew: timedelta = timedelta(seconds=60),
    ) -> "ExecutionEnvelopeVerifier":
        return cls(
            {key_id: path.read_bytes() for key_id, path in keys.items()},
            maximum_ttl=maximum_ttl,
            clock_skew=clock_skew,
        )

    def verify(
        self,
        signed: SignedExecutionEnvelope | str | bytes,
        *,
        now: datetime | None = None,
        expected_task_id: str | None = None,
    ) -> ExecutionEnvelope:
        if isinstance(signed, (str, bytes)):
            signed = SignedExecutionEnvelope.model_validate_json(signed)

        issued_at = _utc(signed.issued_at, field_name="issued_at")
        expires_at = _utc(signed.expires_at, field_name="expires_at")
        current = _utc(now or datetime.now(timezone.utc), field_name="now")
        if expires_at <= issued_at:
            raise ValueError("signed execution envelope has an invalid validity window")
        if expires_at - issued_at > self.maximum_ttl:
            raise ValueError("signed execution envelope exceeds the maximum TTL")
        if issued_at > current + self.clock_skew:
            raise ValueError("signed execution envelope is not valid yet")
        if expires_at < current - self.clock_skew:
            raise ValueError("signed execution envelope has expired")

        envelope = signed.envelope
        if envelope.deadline is not None:
            deadline = _utc(envelope.deadline, field_name="envelope.deadline")
            if expires_at > deadline:
                raise ValueError("signed execution envelope exceeds the task deadline")
            if deadline < current - self.clock_skew:
                raise ValueError("execution-envelope task deadline has passed")
        if expected_task_id is not None and envelope.task_id != expected_task_id:
            raise ValueError("signed execution envelope is for a different task")

        public_key_pem = self.trusted_public_keys.get(signed.key_id)
        if public_key_pem is None:
            raise ValueError("execution-envelope signing key is not trusted")
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("execution-envelope trust key must be Ed25519")
        if signed.public_key_sha256 != _public_key_fingerprint(key):
            raise ValueError("execution-envelope public-key fingerprint does not match")

        try:
            signature = base64.b64decode(signed.signature_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("execution-envelope signature is not valid base64") from exc
        try:
            key.verify(signature, _canonical_json(signed.signing_document()))
        except InvalidSignature as exc:
            raise ValueError("execution-envelope signature verification failed") from exc
        return envelope


def generate_execution_envelope_keypair(private_path: Path, public_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
