"""Typed public contracts for the evaluator-facing product surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ComponentProof(BaseModel):
    name: str
    status: Literal["operational", "attention"]
    proof: str


class ReleaseProof(BaseModel):
    version: str
    environment: str
    commit: str | None
    model_mode: str
    model: str


class DeploymentProof(BaseModel):
    target: str
    project: str | None
    region: str | None
    revision: str | None
    service: str | None
    configuration: str | None
    infrastructure_commit: str | None


class AgentReleaseProof(BaseModel):
    agent_id: str
    display_name: str
    version: str
    owner: str | None
    release_status: Literal["signed"]
    release_digest: str
    tool_count: int
    human_gates: list[str]
    deployment_target: str


class MemoryBankProof(BaseModel):
    configured: bool
    service: str
    generation: str
    tenant_isolation: str
    revision_history: bool
    configuration: dict[str, Any]


class ManagedFleetProof(BaseModel):
    status: Literal["release_qualified", "cloud_verified"]
    cloud_verified: bool
    source: str
    deployed_count: int
    expected_count: int
    agents: list[dict[str, Any]]
    memory_bank: MemoryBankProof
    verification_errors: list[str]
    deployed_at: datetime | None


class FleetProof(BaseModel):
    agent_count: int
    released_count: int
    agents: list[AgentReleaseProof]
    managed_runtime: ManagedFleetProof
    evaluation: dict[str, Any] | None = None


class JudgeOverviewResponse(BaseModel):
    generated_at: datetime
    release: ReleaseProof
    deployment: DeploymentProof
    fleet: FleetProof
    components: list[ComponentProof]


class DelegatedTask(BaseModel):
    task_key: str
    task_type: str
    status: str
    attempts: int
    human_gate: str | None = None


class GuardrailFindingProof(BaseModel):
    direction: str
    detector: str
    category: str
    severity: str
    verdict: str


class DelegatedAgent(BaseModel):
    agent_role: str
    display_name: str
    version: str | None = None
    release_digest: str | None = None
    tasks: list[DelegatedTask]
    task_count: int
    tasks_executed: int
    human_gates: list[str]
    tools_permitted: list[str]
    tools_called: list[str]
    #: Tools actually called over tools the signed package permits. Bounded
    #: authority only means something if the bound is visibly unreached.
    authority_exercised: str
    allowed: int
    denied: int
    denial_reasons: list[str]
    guardrail_findings: list[GuardrailFindingProof]


class DelegationStep(BaseModel):
    step: int
    task_key: str
    task_type: str
    agent_role: str
    status: str
    human_gate: str | None = None
    attempts: int
    last_error: str | None = None


class DelegationEngagement(BaseModel):
    engagement_id: str
    code: str
    title: str
    status: str
    audit_pack_ref: str | None = None
    finding_count: int


class DelegationTotals(BaseModel):
    specialist_agents: int
    tasks: int
    human_gates: int
    gateway_allowed: int
    gateway_denied: int
    guardrail_blocks: int


class DelegationResponse(BaseModel):
    engagement: DelegationEngagement | None
    agents: list[DelegatedAgent]
    handoff: list[DelegationStep]
    totals: DelegationTotals


class GroundTruthCondition(BaseModel):
    id: str
    expected: str
    source: str
    reason: str | None = None


class GroundTruthResponse(BaseModel):
    engagement: str
    audit_period: list[str]
    seeded_conditions: list[GroundTruthCondition]


class ArmorDetectorProof(BaseModel):
    detector: str
    category: str
    severity: str
    match_count: int
    excerpt_digest: str
    detail: str


class PromptInjectionProofResponse(BaseModel):
    source: str
    verdict: str
    tainted: bool
    instruction_neutralized: bool
    canonical_state_mutated: bool
    detectors: list[ArmorDetectorProof]
    sanitized_sha256: str


class IdempotencyProofResponse(BaseModel):
    tenant_id: str
    engagement_id: str
    finding_id: str
    remediation_action_id: str
    remediation_opened_once: bool
    external_correlation_key: str
    external_ticket_ref: str
    external_ticket_filed_once: bool
    final_status: str
    ground_truth_match: dict[str, bool]


class EvaluationSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    passed: bool
    agent_count: int
    passed_agents: int
    case_count: int
    passed_cases: int
