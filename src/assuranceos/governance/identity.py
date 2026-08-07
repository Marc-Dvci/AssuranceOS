"""Agent Identity — zero-trust workload identity for the agent fleet.

Every agent invocation runs as an authenticated workload, not as an anonymous
model call. The control plane mints a short-lived, Ed25519-signed identity
credential bound to one tenant, engagement, task, attempt, and agent release.
The Agent Gateway authenticates that credential before any policy is evaluated,
so a model cannot assert who it is or what it may reach.

Two properties carry the zero-trust guarantee:

* **Binding.** An identity is only valid alongside the signed execution envelope
  it was minted for. Both documents must agree on tenant, agent role, agent
  version, task, and attempt, so a credential captured from one task cannot be
  replayed against another.
* **Intersection.** Granted scopes are the intersection of what the signed agent
  package declares and what the envelope authorises, never the union. Neither
  document can widen the other, so a compromised envelope issuer still cannot
  exceed the released package.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping, Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from ..execution_security import _canonical_json, _public_key_fingerprint, _utc
from ..models import ExecutionEnvelope
from ..registry import AgentPackage

WORKLOAD_URI_SCHEME = "spiffe"
WORKLOAD_TRUST_DOMAIN = "assuranceos"


class AgentIdentityError(Exception):
    """Raised when a workload identity cannot be issued or authenticated."""


def workload_uri(tenant_id: str, agent_role: str, agent_version: str) -> str:
    """Return the SPIFFE-style workload identifier for one released agent role."""
    return (
        f"{WORKLOAD_URI_SCHEME}://{WORKLOAD_TRUST_DOMAIN}"
        f"/tenant/{tenant_id}/agent/{agent_role}/{agent_version}"
    )


class AgentIdentity(BaseModel):
    """The authenticated subject of one bounded agent invocation."""

    model_config = ConfigDict(extra="forbid")

    identity_id: str = Field(default_factory=lambda: f"aid_{uuid4().hex[:16]}")
    workload_uri: str
    tenant_id: str
    agent_role: str
    agent_version: str
    release_id: str | None = None
    engagement_id: str
    task_id: str
    attempt: int = Field(default=1, ge=1)
    lease_owner: str | None = None

    # Effective authority: the intersection of package and envelope, never the union.
    granted_tools: list[str] = []
    granted_evidence_scopes: list[str] = []
    forbidden_actions: list[str] = []

    # Separation of duties. An identity may not act on work its subject produced.
    independence_subject: str | None = None
    independence_constraints: list[str] = []

    human_gate: str | None = None
    model_policy: str = "unspecified"

    def subject_fingerprint(self) -> str:
        """Stable digest of the acting subject, used for independence checks."""
        return hashlib.sha256(
            _canonical_json(
                {
                    "workload_uri": self.workload_uri,
                    "lease_owner": self.lease_owner,
                    "independence_subject": self.independence_subject,
                }
            )
        ).hexdigest()


class SignedAgentIdentity(BaseModel):
    """A short-lived credential proving the control plane issued this identity."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["assurance.signed_agent_identity.v1"] = Field(
        default="assurance.signed_agent_identity.v1",
        alias="schema",
        serialization_alias="schema",
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(min_length=1, max_length=128)
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=16, max_length=128)
    identity: AgentIdentity
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
            "identity": self.identity.model_dump(mode="json"),
        }


class RevocationChecker(Protocol):
    """Consulted on every authentication so a credential can be killed mid-flight."""

    def is_revoked(self, identity_id: str) -> bool: ...


class NullRevocationChecker:
    def is_revoked(self, identity_id: str) -> bool:  # noqa: ARG002 - null object
        return False


@dataclass
class InMemoryRevocationList:
    revoked: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.revoked is None:
            self.revoked = set()

    def revoke(self, identity_id: str) -> None:
        self.revoked.add(identity_id)

    def is_revoked(self, identity_id: str) -> bool:
        return identity_id in self.revoked


def derive_granted_authority(
    package: AgentPackage, envelope: ExecutionEnvelope
) -> tuple[list[str], list[str], list[str]]:
    """Intersect the released package with the execution envelope.

    Returns granted tools, granted evidence scopes, and the union of prohibitions.
    Authority narrows; prohibitions accumulate. Neither input can widen the other.
    """
    declared_tools = {str(item["name"]) for item in package.tools.get("tools", [])}
    granted_tools = sorted(declared_tools & set(envelope.allowed_tools))

    declared_scopes = set(package.policy.get("allowed_evidence_scopes", []))
    if declared_scopes == {"execution_envelope_only"}:
        # The package delegates scope entirely to the envelope rather than
        # enumerating scopes of its own.
        granted_scopes = sorted(set(envelope.allowed_evidence_scopes))
    else:
        granted_scopes = sorted(declared_scopes & set(envelope.allowed_evidence_scopes))

    forbidden = sorted(
        set(package.policy.get("forbidden_actions", [])) | set(envelope.forbidden_actions)
    )
    return granted_tools, granted_scopes, forbidden


@dataclass(frozen=True)
class AgentIdentityIssuer:
    """Control-plane minting of short-lived workload credentials."""

    private_key: Ed25519PrivateKey
    key_id: str
    maximum_ttl: timedelta = timedelta(minutes=15)

    @classmethod
    def from_pem(
        cls,
        path: Path,
        *,
        key_id: str,
        maximum_ttl: timedelta = timedelta(minutes=15),
    ) -> "AgentIdentityIssuer":
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise AgentIdentityError("agent-identity signing key must be Ed25519")
        return cls(private_key=key, key_id=key_id, maximum_ttl=maximum_ttl)

    def issue(
        self,
        package: AgentPackage,
        envelope: ExecutionEnvelope,
        *,
        release_id: str | None = None,
        independence_subject: str | None = None,
        independence_constraints: tuple[str, ...] = (),
        ttl: timedelta | None = None,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> SignedAgentIdentity:
        if envelope.agent_role != package.agent_id:
            raise AgentIdentityError("envelope agent role does not match the agent package")
        if envelope.agent_version != str(package.manifest["version"]):
            raise AgentIdentityError("envelope agent version does not match the released package")

        ttl = ttl or self.maximum_ttl
        if ttl <= timedelta(0):
            raise AgentIdentityError("agent-identity TTL must be positive")
        if ttl > self.maximum_ttl:
            raise AgentIdentityError("agent-identity TTL exceeds the issuer maximum")

        issued_at = _utc(now or datetime.now(timezone.utc), field_name="issued_at")
        expires_at = issued_at + ttl
        if envelope.deadline is not None:
            deadline = _utc(envelope.deadline, field_name="envelope.deadline")
            if deadline <= issued_at:
                raise AgentIdentityError("execution-envelope deadline has already passed")
            expires_at = min(expires_at, deadline)

        granted_tools, granted_scopes, forbidden = derive_granted_authority(package, envelope)
        identity = AgentIdentity(
            workload_uri=workload_uri(
                envelope.tenant_id, envelope.agent_role, envelope.agent_version
            ),
            tenant_id=envelope.tenant_id,
            agent_role=envelope.agent_role,
            agent_version=envelope.agent_version,
            release_id=release_id,
            engagement_id=envelope.engagement_id,
            task_id=envelope.task_id,
            attempt=envelope.attempt_count,
            lease_owner=envelope.lease_owner,
            granted_tools=granted_tools,
            granted_evidence_scopes=granted_scopes,
            forbidden_actions=forbidden,
            independence_subject=independence_subject,
            independence_constraints=list(independence_constraints),
            human_gate=envelope.human_gate,
            model_policy=envelope.model_policy,
        )

        public_key = self.private_key.public_key()
        unsigned = SignedAgentIdentity(
            key_id=self.key_id,
            public_key_sha256=_public_key_fingerprint(public_key),
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce or f"aid_{uuid4().hex}",
            identity=identity,
            signature_base64="pending",
        )
        signature = self.private_key.sign(_canonical_json(unsigned.signing_document()))
        return unsigned.model_copy(
            update={"signature_base64": base64.b64encode(signature).decode("ascii")}
        )


@dataclass(frozen=True)
class AgentIdentityVerifier:
    """Authenticate a workload credential and bind it to its execution envelope."""

    trusted_public_keys: Mapping[str, bytes]
    maximum_ttl: timedelta = timedelta(minutes=15)
    clock_skew: timedelta = timedelta(seconds=60)
    revocations: RevocationChecker = NullRevocationChecker()

    def __post_init__(self) -> None:
        if not self.trusted_public_keys:
            raise AgentIdentityError("at least one agent-identity trust key is required")
        if self.maximum_ttl <= timedelta(0):
            raise AgentIdentityError("maximum agent-identity TTL must be positive")

    def verify(
        self,
        signed: SignedAgentIdentity | str | bytes,
        *,
        envelope: ExecutionEnvelope | None = None,
        now: datetime | None = None,
    ) -> AgentIdentity:
        if isinstance(signed, (str, bytes)):
            signed = SignedAgentIdentity.model_validate_json(signed)

        issued_at = _utc(signed.issued_at, field_name="issued_at")
        expires_at = _utc(signed.expires_at, field_name="expires_at")
        current = _utc(now or datetime.now(timezone.utc), field_name="now")
        if expires_at <= issued_at:
            raise AgentIdentityError("agent identity has an invalid validity window")
        if expires_at - issued_at > self.maximum_ttl:
            raise AgentIdentityError("agent identity exceeds the maximum TTL")
        if issued_at > current + self.clock_skew:
            raise AgentIdentityError("agent identity is not valid yet")
        if expires_at < current - self.clock_skew:
            raise AgentIdentityError("agent identity has expired")

        public_key_pem = self.trusted_public_keys.get(signed.key_id)
        if public_key_pem is None:
            raise AgentIdentityError("agent-identity signing key is not trusted")
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            raise AgentIdentityError("agent-identity trust key must be Ed25519")
        if signed.public_key_sha256 != _public_key_fingerprint(key):
            raise AgentIdentityError("agent-identity public-key fingerprint does not match")

        try:
            signature = base64.b64decode(signed.signature_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise AgentIdentityError("agent-identity signature is not valid base64") from exc
        try:
            key.verify(signature, _canonical_json(signed.signing_document()))
        except InvalidSignature as exc:
            raise AgentIdentityError("agent-identity signature verification failed") from exc

        identity = signed.identity
        if self.revocations.is_revoked(identity.identity_id):
            raise AgentIdentityError("agent identity has been revoked")

        if envelope is not None:
            self._assert_bound(identity, envelope)
        return identity

    @staticmethod
    def _assert_bound(identity: AgentIdentity, envelope: ExecutionEnvelope) -> None:
        """A credential is only valid for the exact invocation it was minted for."""
        mismatches = [
            name
            for name, left, right in (
                ("tenant", identity.tenant_id, envelope.tenant_id),
                ("engagement", identity.engagement_id, envelope.engagement_id),
                ("task", identity.task_id, envelope.task_id),
                ("agent role", identity.agent_role, envelope.agent_role),
                ("agent version", identity.agent_version, envelope.agent_version),
                ("attempt", identity.attempt, envelope.attempt_count),
            )
            if left != right
        ]
        if mismatches:
            raise AgentIdentityError(
                "agent identity is not bound to this execution envelope: "
                + ", ".join(sorted(mismatches))
            )


def generate_agent_identity_keypair(private_path: Path, public_path: Path) -> None:
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
