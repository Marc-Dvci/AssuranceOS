from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.execution_authority import ExecutionAuthority
from assuranceos.execution_security import Ed25519ExecutionEnvelopeSigner, ExecutionEnvelopeVerifier
from assuranceos.orchestration import TaskLease
from assuranceos.registry import AgentRegistry


ROOT = Path(__file__).resolve().parents[1]


def _lease(now: datetime, **updates) -> TaskLease:
    values = {
        "tenant_id": "tnt_1",
        "engagement_id": "eng_1",
        "task_id": "tsk_1",
        "task_key": "skeptic-review",
        "task_type": "agent",
        "assigned_agent_role": "skeptic",
        "attempt_count": 2,
        "lease_owner": "worker_1",
        "lease_expires_at": now + timedelta(minutes=5),
        "input_refs": [],
        "execution_policy": {
            "purpose": "Challenge the proposed SCM observation",
            "allowed_tools": ["evidence.query", "contradictions.search"],
            "allowed_evidence_scopes": ["github:asteria/*", "jira:SCM/*"],
            "token_budget": 12_000,
            "cost_budget_usd": 2.0,
        },
        "model_policy": "audit-high-reasoning-v4",
        "tool_policy": "skeptic-v1",
        "deadline_at": now + timedelta(hours=1),
        "human_gate": "observation_rework_decision",
    }
    values.update(updates)
    return TaskLease(**values)


def test_execution_authority_binds_signed_scope_to_claimed_lease():
    now = datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc)
    private = Ed25519PrivateKey.generate()
    authority = ExecutionAuthority(
        AgentRegistry(ROOT / "agents").load(),
        Ed25519ExecutionEnvelopeSigner(private, "control-plane-v1"),
    )
    signed = authority.issue(_lease(now), now=now)
    envelope = signed.envelope
    assert envelope.task_id == "tsk_1"
    assert envelope.lease_owner == "worker_1"
    assert envelope.attempt_count == 2
    assert envelope.agent_role == "skeptic"
    assert envelope.agent_version == "0.7.0"
    assert envelope.deadline == now + timedelta(minutes=5)
    assert envelope.allowed_tools == ["evidence.query", "contradictions.search"]
    assert envelope.token_budget == 12_000

    public_pem = private.public_key().public_bytes_raw()
    # Exercise the normal PEM trust path used by ADK.
    from cryptography.hazmat.primitives import serialization

    pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    verified = ExecutionEnvelopeVerifier({"control-plane-v1": pem}).verify(signed, now=now)
    assert verified.task_id == envelope.task_id
    assert public_pem


def test_execution_authority_fails_closed_on_missing_or_excessive_policy():
    now = datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc)
    authority = ExecutionAuthority(
        AgentRegistry(ROOT / "agents").load(),
        Ed25519ExecutionEnvelopeSigner(Ed25519PrivateKey.generate(), "control-plane-v1"),
    )
    with pytest.raises(ValueError, match="allowed_tools"):
        authority.issue(
            _lease(
                now,
                execution_policy={
                    "purpose": "Review",
                    "allowed_evidence_scopes": [],
                },
            ),
            now=now,
        )
    with pytest.raises(ValueError, match="ceiling"):
        authority.issue(
            _lease(
                now,
                execution_policy={
                    "purpose": "Review",
                    "allowed_tools": ["evidence.query"],
                    "allowed_evidence_scopes": [],
                    "token_budget": 999_999,
                },
            ),
            now=now,
        )
    with pytest.raises(ValueError, match="assigned to an agent"):
        authority.issue(_lease(now, assigned_agent_role=None), now=now)
    with pytest.raises(ValueError, match="lease has expired"):
        authority.issue(_lease(now, lease_expires_at=now), now=now)
