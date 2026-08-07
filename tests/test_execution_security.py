from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.execution_security import (
    Ed25519ExecutionEnvelopeSigner,
    ExecutionEnvelopeVerifier,
    SignedExecutionEnvelope,
)
from assuranceos.models import ExecutionEnvelope


def _keys():
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem


def _envelope(*, deadline: datetime | None = None) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id="tsk_signed_1",
        engagement_id="eng_signed_1",
        tenant_id="tnt_signed_1",
        agent_role="skeptic",
        agent_version="0.7.0",
        purpose="Challenge the proposed observation",
        allowed_evidence_scopes=["github:asteria/*"],
        allowed_tools=["evidence.query"],
        forbidden_actions=["source.write"],
        model_policy="audit-high-reasoning-v4",
        deadline=deadline,
    )


def test_signed_execution_envelope_round_trip_and_task_binding():
    private, public_pem = _keys()
    now = datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc)
    signer = Ed25519ExecutionEnvelopeSigner(private, "control-plane-v1")
    signed = signer.issue(_envelope(), ttl=timedelta(minutes=10), now=now, nonce="env_test_nonce_0001")

    verifier = ExecutionEnvelopeVerifier(
        {"control-plane-v1": public_pem},
        maximum_ttl=timedelta(hours=1),
        clock_skew=timedelta(0),
    )
    verified = verifier.verify(
        signed.model_dump_json(by_alias=True),
        now=now + timedelta(minutes=5),
        expected_task_id="tsk_signed_1",
    )
    assert verified == signed.envelope
    with pytest.raises(ValueError, match="different task"):
        verifier.verify(signed, now=now, expected_task_id="tsk_other")


def test_tampering_unknown_key_and_invalid_signature_are_rejected():
    private, public_pem = _keys()
    now = datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc)
    signed = Ed25519ExecutionEnvelopeSigner(private, "control-plane-v1").issue(
        _envelope(), now=now
    )
    verifier = ExecutionEnvelopeVerifier({"control-plane-v1": public_pem})

    tampered = signed.model_copy(
        update={
            "envelope": signed.envelope.model_copy(
                update={"allowed_tools": ["credentials.read"]}
            )
        }
    )
    with pytest.raises(ValueError, match="signature verification failed"):
        verifier.verify(tampered, now=now)

    unknown = signed.model_copy(update={"key_id": "unknown"})
    with pytest.raises(ValueError, match="not trusted"):
        verifier.verify(unknown, now=now)

    malformed = signed.model_copy(update={"signature_base64": "not-base64!"})
    with pytest.raises(ValueError, match="not valid base64"):
        verifier.verify(malformed, now=now)

    raw = base64.b64decode(signed.signature_base64)
    invalid = signed.model_copy(
        update={"signature_base64": base64.b64encode(bytes([raw[0] ^ 1]) + raw[1:]).decode()}
    )
    with pytest.raises(ValueError, match="verification failed"):
        verifier.verify(invalid, now=now)


def test_validity_window_deadline_and_maximum_ttl_are_enforced():
    private, public_pem = _keys()
    now = datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc)
    signer = Ed25519ExecutionEnvelopeSigner(private, "control-plane-v1")
    verifier = ExecutionEnvelopeVerifier(
        {"control-plane-v1": public_pem},
        maximum_ttl=timedelta(minutes=30),
        clock_skew=timedelta(0),
    )

    signed = signer.issue(_envelope(), ttl=timedelta(minutes=5), now=now)
    with pytest.raises(ValueError, match="not valid yet"):
        verifier.verify(signed, now=now - timedelta(seconds=1))
    with pytest.raises(ValueError, match="has expired"):
        verifier.verify(signed, now=now + timedelta(minutes=5, seconds=1))

    overlong = signer.issue(_envelope(), ttl=timedelta(hours=1), now=now)
    with pytest.raises(ValueError, match="maximum TTL"):
        verifier.verify(overlong, now=now)

    deadline = now + timedelta(minutes=3)
    bounded = signer.issue(_envelope(deadline=deadline), ttl=timedelta(minutes=10), now=now)
    assert bounded.expires_at == deadline
    assert verifier.verify(bounded, now=now).deadline == deadline

    with pytest.raises(ValueError, match="already passed"):
        signer.issue(_envelope(deadline=now), now=now)


def test_signed_envelope_schema_rejects_unknown_fields():
    private, _ = _keys()
    signed = Ed25519ExecutionEnvelopeSigner(private, "control-plane-v1").issue(_envelope())
    payload = signed.model_dump(mode="json", by_alias=True)
    payload["untrusted"] = True
    with pytest.raises(ValueError):
        SignedExecutionEnvelope.model_validate(payload)
