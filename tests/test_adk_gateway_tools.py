"""ADK tools route through the same enforcement point as the in-process runtime.

In ADK the tool list *is* the security boundary: whatever is in it, the model can
call. These cases check that what is in it are gateway shims rather than direct
implementations, so a tool the gateway would deny is denied on this path too, for
the same recorded reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.adk import build_adk_agent, build_gateway_tools
from assuranceos.governance import (
    AgentGateway,
    AgentIdentityIssuer,
    AgentIdentityVerifier,
    BoundedTool,
    ModelArmor,
)
from assuranceos.models import ExecutionEnvelope
from assuranceos.registry import AgentRegistry

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROLE = "evidence-custodian"


@pytest.fixture(scope="module")
def package():
    return AgentRegistry(ROOT / "agents").load()[AGENT_ROLE]


@pytest.fixture
def wiring(package):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    gateway = AgentGateway(
        identity_verifier=AgentIdentityVerifier({"adk-v1": public}), armor=ModelArmor()
    )
    gateway.register_tool(
        AGENT_ROLE,
        BoundedTool(
            "evidence.capture",
            lambda *, arguments, identity, envelope: f"captured {arguments.get('locator', '')}",
        ),
    )
    issuer = AgentIdentityIssuer(private_key=key, key_id="adk-v1")
    envelope = ExecutionEnvelope(
        engagement_id="eng_1",
        tenant_id="tnt_a",
        agent_role=AGENT_ROLE,
        agent_version=str(package.manifest["version"]),
        purpose="collect change evidence",
        allowed_evidence_scopes=["engagement"],
        allowed_tools=["evidence.capture"],
        forbidden_actions=list(package.policy.get("forbidden_actions", [])),
        model_policy="flash",
    )
    return gateway, issuer, envelope


def tools_by_name(tools):
    return {tool.__name__: tool for tool in tools}


def test_a_permitted_call_reaches_the_bounded_handler(package, wiring):
    gateway, issuer, envelope = wiring
    tools = tools_by_name(
        build_gateway_tools(
            package, gateway=gateway, identity_issuer=issuer, envelope=envelope
        )
    )
    reply = json.loads(tools["evidence_capture"](json.dumps({"locator": "gh://pr/42"})))

    assert reply["allowed"] is True
    assert reply["result"] == "captured gh://pr/42"


def test_a_tool_outside_the_envelope_is_denied_with_its_reason(package, wiring):
    """The model gets a readable denial rather than an exception, and can adapt."""
    gateway, issuer, envelope = wiring
    tools = tools_by_name(
        build_gateway_tools(
            package, gateway=gateway, identity_issuer=issuer, envelope=envelope
        )
    )
    # The package declares more tools than this envelope grants.
    outside = set(tools) - {"evidence_capture"}
    assert outside, "the package should declare more tools than the envelope grants"

    reply = json.loads(tools[sorted(outside)[0]]("{}"))
    assert reply["allowed"] is False
    assert reply["stage"] in {"policy", "routing"}
    assert reply["decision_id"].startswith("gwd_")


def test_poisoned_arguments_are_blocked_by_the_same_guardrails(package, wiring):
    gateway, issuer, envelope = wiring
    tools = tools_by_name(
        build_gateway_tools(
            package, gateway=gateway, identity_issuer=issuer, envelope=envelope
        )
    )
    reply = json.loads(
        tools["evidence_capture"](json.dumps({"locator": "../../etc/shadow"}))
    )
    assert reply["allowed"] is False
    assert reply["stage"] == "model_armor"


def test_malformed_arguments_never_reach_the_gateway(package, wiring):
    gateway, issuer, envelope = wiring
    tools = tools_by_name(
        build_gateway_tools(
            package, gateway=gateway, identity_issuer=issuer, envelope=envelope
        )
    )
    before = len(gateway.decisions)
    reply = json.loads(tools["evidence_capture"]("not json at all"))

    assert reply["allowed"] is False
    assert reply["stage"] == "arguments"
    assert len(gateway.decisions) == before


def test_every_denial_is_recorded_on_the_gateway(package, wiring):
    """The ADK path leaves the same attributable trail as the in-process one."""
    gateway, issuer, envelope = wiring
    tools = tools_by_name(
        build_gateway_tools(
            package, gateway=gateway, identity_issuer=issuer, envelope=envelope
        )
    )
    tools["evidence_capture"](json.dumps({"locator": "../../etc/shadow"}))

    denials = [d for d in gateway.decisions if d.decision == "deny"]
    assert denials
    assert denials[-1].tool_name == "evidence.capture"
    assert denials[-1].task_id == envelope.task_id


def test_an_agent_without_a_gateway_carries_only_envelope_validation(
    package, monkeypatch
):
    """The safe default: no bound gateway means nothing the model may reach."""

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

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    agent = build_adk_agent(
        package.path, "gemini-3.7-flash", trusted_execution_keys={"cp": public}
    )
    assert len(agent.tools) == 1


def test_a_bound_agent_exposes_the_declared_tools(package, wiring, monkeypatch):
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

    gateway, issuer, envelope = wiring
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    agent = build_adk_agent(
        package.path,
        "gemini-3.7-flash",
        trusted_execution_keys={"cp": public},
        gateway=gateway,
        identity_issuer=issuer,
        envelope=envelope,
    )

    declared = [
        item["name"] for item in package.tools["tools"] if item.get("name")
    ]
    # Envelope validation plus one shim per declared tool.
    assert len(agent.tools) == 1 + len(declared)
    assert "evidence_capture" in {tool.__name__ for tool in agent.tools[1:]}

    # Construction alone did not catch a previous integration defect: the
    # supplied AgentGateway was replaced by the package-policy evaluator, so the
    # callable existed but had no invoke method. Exercise the deployed shim.
    capture = tools_by_name(agent.tools[1:])["evidence_capture"]
    reply = json.loads(capture(json.dumps({"locator": "gh://pr/42"})))
    assert reply["allowed"] is True


def test_deployed_agent_carries_no_platform_specific_path():
    """A WindowsPath in the pickle kills the Linux runtime after it is billed.

    Agent Engine cloudpickles the agent object. A concrete Path pickles as the
    deploying platform's flavour, so deploying from Windows produced
    "NotImplementedError: cannot instantiate 'WindowsPath' on your system" when
    the container loaded its own agent. The package therefore has to travel with
    a platform-neutral path.
    """

    import pickle
    from dataclasses import replace
    from pathlib import PurePosixPath, PureWindowsPath

    from assuranceos.registry import AgentPackage

    package = AgentPackage(
        agent_id="agent-a",
        path=PureWindowsPath(r"D:\repo\agents\agent-a"),
        manifest={"version": "1.0.0"},
        tools={"tools": []},
        policy={},
        model_profiles={},
        evaluations={},
        release={"package_sha256": "abc123"},
    )

    portable = replace(package, path=PurePosixPath(package.path.as_posix()))

    assert portable.path == PurePosixPath("D:/repo/agents/agent-a")
    # The whole point: it survives a round trip on a machine of another flavour.
    assert pickle.loads(pickle.dumps(portable)).path == portable.path
    assert "WindowsPath" not in repr(type(portable.path))
