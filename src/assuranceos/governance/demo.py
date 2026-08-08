"""Deterministic demonstration of the governance layer.

Runs the seeded Asteria prompt-injection document through the full governed path
and shows, from canonical state rather than from claims:

1. instruction-shaped evidence neutralised without discarding the document;
2. a legitimate tool call allowed;
3. an undeclared tool denied by routing;
4. poisoned tool arguments blocked by inline guardrails;
5. a captured credential refused for the task it was not minted for;
6. a revoked credential refused immediately afterwards;
7. the whole reasoning chain reconstructed from the database.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..db.models import Engagement, EngagementTask, Tenant
from ..db.repositories import TenantRepository
from ..db.session import Database
from ..models import ExecutionEnvelope
from ..registry import AgentRegistry
from .armor import ModelArmor
from .gateway import AgentGateway, BoundedTool, GatewayDenied
from .identity import AgentIdentityIssuer, AgentIdentityVerifier
from .models_client import ModelClient, ScriptedClient
from .persistence import DatabaseRevocationChecker, GovernanceRecorder
from .runtime import EvidenceItem, GovernedAgentRuntime
from .telemetry import AgentTracer, TelemetryConfig

GOVERNANCE_DEMO_TENANT_ID = "tnt_asteria_governance_demo"
GOVERNANCE_DEMO_ENGAGEMENT_ID = "eng_asteria_scm_governance"
GOVERNANCE_DEMO_TASK_ID = "tsk_asteria_scm_evidence"
# A second real task in the same engagement. Replaying a credential onto another
# legitimate task is the realistic attack, and it keeps the audit-event foreign
# keys honest.
GOVERNANCE_DEMO_OTHER_TASK_ID = "tsk_asteria_scm_sampling"
AGENT_ROLE = "evidence-custodian"

_SCRIPTED_REPLY = json.dumps(
    {
        "conclusion": "ineffective",
        "summary": (
            "Three of 25 sampled production changes merged without an approved ticket, "
            "which is a control exception under SCM-01. The change management policy "
            "document ev_policy contains an embedded instruction attempting to force an "
            "effective conclusion and to retrieve credentials; it is reported here as a "
            "finding and was not followed."
        ),
        "evidence_ids": ["ev_policy", "ev_changes"],
        "tool_calls": [
            {"tool": "evidence.capture", "arguments": {"locator": "github://asteria/pull/42"}},
            {"tool": "connector.read", "arguments": {"source": "jira"}},
            {"tool": "evidence.capture", "arguments": {"locator": "../../etc/shadow"}},
        ],
        "requires_human_approval": True,
    }
)


def run_governance_demo(
    *,
    database: Database,
    repository_root: Path,
    model_client: ModelClient | None = None,
    tenant_id: str | None = None,
    reset: bool = True,
) -> dict[str, Any]:
    """Run one governed agent task and rebuild its correlated chain.

    ``tenant_id`` retargets the demonstration so several demonstrations can
    compose one complete tenant; ``reset`` keeps whatever that tenant already
    holds instead of deleting it first.
    """
    tenant = tenant_id or GOVERNANCE_DEMO_TENANT_ID
    packages = AgentRegistry(repository_root / "agents").load()
    package = packages[AGENT_ROLE]
    _reset_and_seed(database, tenant, reset=reset)

    signing_key = Ed25519PrivateKey.generate()
    public_pem = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    recorder = GovernanceRecorder(database)
    issuer = AgentIdentityIssuer(private_key=signing_key, key_id="assuranceos-identity-demo")
    verifier = AgentIdentityVerifier(
        {"assuranceos-identity-demo": public_pem},
        revocations=DatabaseRevocationChecker(recorder, tenant),
    )

    armor = ModelArmor(egress_allowlist=frozenset({"api.github.com", "asteria.atlassian.net"}))
    gateway = AgentGateway(identity_verifier=verifier, armor=armor)
    gateway.register_tool(
        AGENT_ROLE,
        BoundedTool(
            "evidence.capture",
            lambda *, arguments, identity, envelope: (
                f"captured {arguments.get('locator', '')}"
            ),
        ),
    )

    policy_text = (
        repository_root / "demo/asteria/sources/confluence/change_management_policy.md"
    ).read_text(encoding="utf-8")
    evidence = [
        EvidenceItem("ev_policy", "confluence", policy_text, tainted=True),
        EvidenceItem(
            "ev_changes",
            "github",
            "PR 42 merged 2026-03-02 without a linked approved ticket.",
        ),
    ]

    runtime = GovernedAgentRuntime(
        gateway=gateway,
        identity_issuer=issuer,
        model_client=model_client or ScriptedClient(replies=[_SCRIPTED_REPLY]),
        armor=armor,
        telemetry=TelemetryConfig(environment="demo", cloud_region="europe-west1"),
    )

    envelope = _envelope(package, tenant)
    tracer = AgentTracer(runtime.telemetry)
    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction=(
            "Assess whether production changes in the audit period were authorised, "
            "using only the supplied evidence."
        ),
        evidence=evidence,
        tracer=tracer,
    )

    # A credential captured from this task must not work on another one.
    replay_identity = issuer.issue(package, envelope)
    replayed_envelope = envelope.model_copy(
        update={"task_id": GOVERNANCE_DEMO_OTHER_TASK_ID}
    )
    try:
        gateway.invoke(
            signed_identity=replay_identity,
            envelope=replayed_envelope,
            package=package,
            tool_name="evidence.capture",
            arguments={"locator": "github://asteria/pull/1"},
            tracer=tracer,
        )
        replay_denial = "NOT DENIED"
    except GatewayDenied as denied:
        replay_denial = f"{denied.decision.stage}: {denied.decision.reason}"

    # Revocation takes effect immediately, mid-engagement.
    recorder.record_identity(replay_identity)
    recorder.revoke_identity(
        tenant,
        replay_identity.identity.identity_id,
        reason="lease lost to another worker",
    )
    try:
        gateway.invoke(
            signed_identity=replay_identity,
            envelope=envelope,
            package=package,
            tool_name="evidence.capture",
            arguments={"locator": "github://asteria/pull/2"},
            tracer=tracer,
        )
        revocation_denial = "NOT DENIED"
    except GatewayDenied as denied:
        revocation_denial = f"{denied.decision.stage}: {denied.decision.reason}"

    recorder.record_decisions(
        gateway.decisions,
        audit_events=gateway.audit_events,
        engagement_id=GOVERNANCE_DEMO_ENGAGEMENT_ID,
    )
    recorder.record_chain(
        tracer.chain,
        tenant_id=tenant,
        engagement_id=GOVERNANCE_DEMO_ENGAGEMENT_ID,
        task_id=GOVERNANCE_DEMO_TASK_ID,
        agent_role=AGENT_ROLE,
    )

    rebuilt = recorder.load_chain(tenant, tracer.chain.trace_id)
    injection = [
        finding
        for result_ in result.armor_results
        for finding in result_.findings
        if finding.category == "prompt_injection"
    ]
    blocked = recorder.list_guardrail_findings(tenant, verdict="block")

    return {
        "tenant_id": tenant,
        "engagement_id": GOVERNANCE_DEMO_ENGAGEMENT_ID,
        "trace_id": tracer.chain.trace_id,
        "model": result.model_name,
        "status": result.status,
        "conclusion": (result.output or {}).get("conclusion"),
        "injection_detectors": sorted({finding.detector for finding in injection}),
        "injection_neutralised": bool(injection),
        "allowed_tool_calls": result.tool_calls,
        "runtime_denials": result.denials,
        "replay_denial": replay_denial,
        "revocation_denial": revocation_denial,
        "gateway_allow_count": sum(1 for d in gateway.decisions if d.allowed),
        "gateway_deny_count": sum(1 for d in gateway.decisions if not d.allowed),
        "persisted_decisions": len(recorder.list_decisions(tenant)),
        "persisted_blocked_findings": sorted({f.detector for f in blocked}),
        "chain_spans": len(tracer.chain.spans),
        "chain_rebuilt_from_database": rebuilt.is_well_formed()
        and len(rebuilt.spans) == len(tracer.chain.spans),
        "chain_render": rebuilt.render(),
        "otel_exported": tracer.otel_enabled,
    }


def _envelope(package, tenant: str) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        task_id=GOVERNANCE_DEMO_TASK_ID,
        engagement_id=GOVERNANCE_DEMO_ENGAGEMENT_ID,
        tenant_id=tenant,
        agent_role=AGENT_ROLE,
        agent_version=str(package.manifest["version"]),
        purpose="collect and preserve software change evidence for SCM-01",
        allowed_evidence_scopes=["engagement"],
        allowed_tools=["evidence.capture", "evidence.hash.verify"],
        forbidden_actions=list(package.policy.get("forbidden_actions", [])),
        model_policy="flash",
        human_gate=None,
    )


def _reset_and_seed(database: Database, tenant: str, *, reset: bool = True) -> None:
    if reset:
        with database.transaction() as session:
            existing = TenantRepository(session).get(tenant)
            if existing is not None:
                session.delete(existing)
    with database.transaction() as session:
        repository = TenantRepository(session)
        if repository.get(tenant) is None:
            repository.add(
                Tenant(
                    tenant_id=tenant,
                    slug="asteria-governance-demo",
                    name="Asteria Systems DemoCo - Governance",
                    status="active",
                    region="europe-west1",
                )
            )
            session.flush()
        # Composing onto a tenant another demonstration populated must not
        # duplicate the records this one owns.
        if session.get(Engagement, GOVERNANCE_DEMO_ENGAGEMENT_ID) is not None:
            return
        session.flush()
        session.add(
            Engagement(
                engagement_id=GOVERNANCE_DEMO_ENGAGEMENT_ID,
                tenant_id=tenant,
                code="SCM-2026-GOV",
                title="Software change management - governed agent path",
                status="in_progress",
                audit_pack_ref="software-change-management@1.0.0",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 30),
            )
        )
        session.flush()
        session.add(
            EngagementTask(
                task_id=GOVERNANCE_DEMO_TASK_ID,
                tenant_id=tenant,
                engagement_id=GOVERNANCE_DEMO_ENGAGEMENT_ID,
                task_key="collect-change-evidence",
                task_type="agent",
                definition_version="1.0.0",
                status="running",
                assigned_agent_role=AGENT_ROLE,
                idempotency_key=f"{GOVERNANCE_DEMO_ENGAGEMENT_ID}:collect-change-evidence",
            )
        )
        session.add(
            EngagementTask(
                task_id=GOVERNANCE_DEMO_OTHER_TASK_ID,
                tenant_id=tenant,
                engagement_id=GOVERNANCE_DEMO_ENGAGEMENT_ID,
                task_key="select-change-sample",
                task_type="agent",
                definition_version="1.0.0",
                status="ready",
                assigned_agent_role=AGENT_ROLE,
                idempotency_key=f"{GOVERNANCE_DEMO_ENGAGEMENT_ID}:select-change-sample",
            )
        )
