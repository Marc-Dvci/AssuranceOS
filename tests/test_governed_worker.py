"""The governed runtime mounted on the durable orchestration task path.

These cases exist because a control that a caller has to remember to invoke is
not a control. They check the mounting itself: that authority is derived from the
lease rather than from anything the model said, that a governed run's outcome
maps onto the orchestrator's retry semantics correctly, and that a failed run is
still reconstructable afterwards.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.governance import (
    AgentGateway,
    AgentIdentityIssuer,
    AgentIdentityVerifier,
    BoundedTool,
    GovernedAgentTaskHandler,
    ModelArmor,
    envelope_from_lease,
    evidence_from_records,
)
from assuranceos.governance.models_client import ScriptedClient
from assuranceos.orchestration.definitions import FailureClass, TaskLease
from assuranceos.orchestration.exceptions import PermanentTaskError, RetryableTaskError
from assuranceos.registry import AgentRegistry

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROLE = "evidence-custodian"


@pytest.fixture(scope="module")
def registry():
    return AgentRegistry(ROOT / "agents")


def make_lease(**overrides) -> TaskLease:
    defaults = dict(
        tenant_id="tnt_a",
        engagement_id="eng_1",
        task_id="tsk_1",
        task_key="collect-change-evidence",
        task_type="agent.evidence",
        assigned_agent_role=AGENT_ROLE,
        attempt_count=1,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model_policy="flash",
    )
    defaults.update(overrides)
    return TaskLease(**defaults)


GOOD_REPLY = json.dumps(
    {
        "conclusion": "ineffective",
        "summary": "Three changes merged without approval; see ev_changes.",
        "evidence_ids": ["ev_changes"],
        "tool_calls": [{"tool": "evidence.capture", "arguments": {"locator": "gh://pr/42"}}],
        "requires_human_approval": True,
    }
)

EVIDENCE = [{"evidence_id": "ev_changes", "source_type": "jira", "content": "PR 42 merged."}]


def build_handler(registry, reply: str, **overrides) -> GovernedAgentTaskHandler:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    armor = ModelArmor()
    gateway = AgentGateway(
        identity_verifier=AgentIdentityVerifier({"w-v1": public}), armor=armor
    )
    gateway.register_tool(AGENT_ROLE, BoundedTool("evidence.capture", lambda **_: "captured"))
    client = ScriptedClient(replies=[reply])
    for name, value in overrides.pop("client", {}).items():
        setattr(client, name, value)
    return GovernedAgentTaskHandler(
        registry=registry,
        gateway=gateway,
        identity_issuer=AgentIdentityIssuer(private_key=key, key_id="w-v1"),
        model_client=client,
        evidence_loader=lambda _: evidence_from_records(EVIDENCE),
        **overrides,
    )


def test_authority_comes_from_the_lease_not_the_model(registry):
    """The envelope is built from canonical orchestration state.

    This is the direction the whole design rests on: the control plane says what
    a task may do, and the model only proposes inside it.
    """
    lease = make_lease(human_gate="engagement_partner", model_policy="pro")
    envelope = envelope_from_lease(
        lease,
        agent_version="0.7.0",
        allowed_tools=["evidence.capture"],
        allowed_evidence_scopes=["engagement"],
        forbidden_actions=["credential_export"],
        purpose="collect change evidence",
    )

    assert envelope.task_id == lease.task_id
    assert envelope.tenant_id == lease.tenant_id
    assert envelope.agent_role == AGENT_ROLE
    assert envelope.human_gate == "engagement_partner"
    assert envelope.model_policy == "pro"
    assert envelope.lease_owner == "worker-1"
    assert envelope.attempt_count == 1


def test_a_task_with_no_assigned_role_cannot_be_executed(registry):
    with pytest.raises(PermanentTaskError, match="no assigned agent role"):
        envelope_from_lease(
            make_lease(assigned_agent_role=None),
            agent_version="0.7.0",
            allowed_tools=[],
            allowed_evidence_scopes=[],
            forbidden_actions=[],
            purpose="x",
        )


def test_an_unknown_role_fails_permanently(registry):
    handler = build_handler(registry, GOOD_REPLY)
    with pytest.raises(PermanentTaskError, match="no released agent package"):
        handler(make_lease(assigned_agent_role="not-a-real-agent"))


def test_a_governed_task_completes_and_reports_its_trace(registry):
    handler = build_handler(registry, GOOD_REPLY)
    result = handler(make_lease())

    assert result.result["status"] == "completed"
    assert result.result["tool_calls"] == ["evidence.capture"]
    assert result.output_refs[0].startswith("agent-run:")


def test_an_unreachable_model_is_retryable(registry):
    """The only failure worth another attempt."""

    class BrokenClient:
        model_name = "broken"

        def generate(self, **_):
            raise TimeoutError("connection timed out")

    handler = build_handler(registry, GOOD_REPLY)
    handler.model_client = BrokenClient()

    with pytest.raises(RetryableTaskError) as caught:
        handler(make_lease())
    assert caught.value.failure_class is FailureClass.MODEL_TIMEOUT


def test_an_inadmissible_conclusion_is_permanent(registry):
    """Retrying reproduces it exactly, so it must surface to an operator instead."""
    handler = build_handler(
        registry,
        json.dumps(
            {
                "conclusion": "ineffective",
                "summary": "Changes merged without approval.",
                "evidence_ids": ["ev_not_supplied"],
                "tool_calls": [],
            }
        ),
    )
    with pytest.raises(PermanentTaskError) as caught:
        handler(make_lease())
    assert caught.value.failure_class is FailureClass.MALFORMED_STRUCTURED_OUTPUT
    assert "never supplied" in str(caught.value)


def test_truncation_is_a_configuration_fault_not_a_transient_one(registry):
    """No number of retries makes an output ceiling large enough."""
    handler = build_handler(
        registry,
        "<think>Weighing the change population and whether the policy",
        client={"finish_reasons": ["length"]},
    )
    with pytest.raises(PermanentTaskError) as caught:
        handler(make_lease())
    assert caught.value.failure_class is FailureClass.CONFIGURATION_ERROR
    assert "model_truncated" in str(caught.value)


def test_a_failed_run_is_still_recorded(registry, tmp_path):
    """The denied run is exactly the one an auditor needs to reconstruct."""
    from assuranceos.db.session import Database
    from assuranceos.governance.persistence import GovernanceRecorder

    from datetime import date

    from assuranceos.db.models import Engagement, EngagementTask, Tenant

    database = Database.from_sqlite_path(tmp_path / "gov.db")
    database.create_schema()
    try:
        # Spans reference canonical tenant, engagement, and task rows, so the
        # lease this test replays has to correspond to real orchestration state.
        with database.transaction() as session:
            session.add(Tenant(tenant_id="tnt_a", slug="a", name="A"))
            session.flush()
            session.add(
                Engagement(
                    engagement_id="eng_1",
                    tenant_id="tnt_a",
                    code="SCM-1",
                    title="SCM",
                    status="in_progress",
                    audit_pack_ref="software-change-management@1.0.0",
                    period_start=date(2026, 1, 1),
                    period_end=date(2026, 6, 30),
                )
            )
            session.flush()
            session.add(
                EngagementTask(
                    task_id="tsk_1",
                    tenant_id="tnt_a",
                    engagement_id="eng_1",
                    task_key="collect-change-evidence",
                    task_type="agent",
                    definition_version="1.0.0",
                    status="running",
                    assigned_agent_role=AGENT_ROLE,
                    idempotency_key="eng_1:collect-change-evidence",
                )
            )

        handler = build_handler(
            registry,
            json.dumps(
                {
                    "conclusion": "ineffective",
                    "summary": "Changes merged without approval.",
                    "evidence_ids": ["ev_never_collected"],
                    "tool_calls": [],
                }
            ),
            recorder=GovernanceRecorder(database),
        )
        with pytest.raises(PermanentTaskError):
            handler(make_lease())

        from assuranceos.db.models import ReasoningSpanRecord
        from sqlalchemy import select

        with database.read_session() as session:
            spans = list(session.scalars(select(ReasoningSpanRecord)))
        assert spans, "a failed governed run must still leave a reconstructable chain"
        assert {span.task_id for span in spans} == {"tsk_1"}
    finally:
        database.dispose()
