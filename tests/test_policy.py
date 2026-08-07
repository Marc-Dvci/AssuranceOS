from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.adk import build_adk_agent
from assuranceos.execution_security import Ed25519ExecutionEnvelopeSigner
from assuranceos.models import ExecutionEnvelope
from assuranceos.policy import PolicyGateway
from assuranceos.registry import AgentPackage, AgentRegistry


ROOT = Path(__file__).resolve().parents[1]


def _package(agent_id: str) -> AgentPackage:
    return AgentRegistry(ROOT / "agents").load()[agent_id]


def _envelope(package: AgentPackage, **updates) -> ExecutionEnvelope:
    values = {
        "engagement_id": "eng_1",
        "tenant_id": "tnt_1",
        "agent_role": package.agent_id,
        "agent_version": str(package.manifest["version"]),
        "purpose": "Execute the approved task",
        "allowed_evidence_scopes": ["github:asteria/*"],
        "allowed_tools": [package.tools["tools"][0]["name"]],
        "forbidden_actions": package.policy["forbidden_actions"],
        "model_policy": "mock",
    }
    values.update(updates)
    return ExecutionEnvelope(**values)


def test_undeclared_tool_is_denied():
    package = _package("skeptic")
    envelope = _envelope(package, allowed_tools=["credentials.read"])
    decision = PolicyGateway().authorize(package, envelope)
    assert not decision.allowed
    assert "credentials.read" in decision.denied_tools


def test_identity_version_and_prohibition_weakening_are_denied():
    package = _package("skeptic")
    gateway = PolicyGateway()
    assert not gateway.authorize(
        package, _envelope(package, agent_role="quality-reviewer")
    ).allowed
    assert not gateway.authorize(package, _envelope(package, agent_version="0.0.0")).allowed
    assert not gateway.authorize(package, _envelope(package, forbidden_actions=[])).allowed


def test_side_effect_tool_requires_declared_human_gate():
    package = _package("remediation-coordinator")
    tool = "external_action.create"
    gateway = PolicyGateway()
    without_gate = _envelope(package, allowed_tools=[tool], human_gate=None)
    assert not gateway.authorize_tool(package, without_gate, tool).allowed

    with_gate = without_gate.model_copy(update={"human_gate": "external_action_approval"})
    assert gateway.authorize_tool(package, with_gate, tool).allowed
    assert not gateway.authorize_tool(package, with_gate, "unknown.tool").allowed


def test_adk_agent_accepts_only_authenticated_execution_envelopes(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    google = ModuleType("google")
    adk = ModuleType("google.adk")
    agents = ModuleType("google.adk.agents")
    agents.Agent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", adk)
    monkeypatch.setitem(sys.modules, "google.adk.agents", agents)

    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    package = _package("skeptic")
    agent = build_adk_agent(
        package.path,
        "gemini-test",
        trusted_execution_keys={"test-control-plane": public_pem},
    )
    assert agent.name == "skeptic"
    assert agent.model == "gemini-test"
    assert len(agent.tools) == 1

    now = datetime.now(timezone.utc)
    envelope = _envelope(package, deadline=now + timedelta(hours=1))
    signed = Ed25519ExecutionEnvelopeSigner(private, "test-control-plane").issue(
        envelope, now=now
    )
    authority = agent.tools[0](signed.model_dump_json(by_alias=True))
    assert authority["authorized"] is True
    assert authority["agent_role"] == "skeptic"

    with pytest.raises(ValueError):
        agent.tools[0](envelope.model_dump_json())

    tampered = signed.model_copy(
        update={"envelope": envelope.model_copy(update={"allowed_tools": ["credentials.read"]})}
    )
    with pytest.raises(ValueError, match="signature verification failed"):
        agent.tools[0](tampered.model_dump_json(by_alias=True))
