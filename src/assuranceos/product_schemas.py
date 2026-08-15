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


class CatalogueTool(BaseModel):
    name: str
    description: str
    side_effect: str
    #: Whether the declared side effect changes anything outside the platform.
    #: A catalogue listing tool names alone reads a read-only agent and a
    #: writing one identically.
    writes: bool
    requires_human_confirmation: bool


class CatalogueBudgets(BaseModel):
    token_budget: int | None = None
    cost_budget_usd: float | None = None
    latency_seconds: int | None = None
    max_concurrency: int | None = None


class CatalogueRelease(BaseModel):
    package_sha256: str | None = None
    prompt_hash: str | None = None
    release_key_id: str | None = None
    reviewers: list[str] = []
    released_at: str | None = None


class CatalogueAgent(BaseModel):
    agent_id: str
    display_name: str
    version: str | None = None
    status: str
    domain: str
    accountable_owner: str | None = None
    #: What the agent is for, in the signed manifest's own words.
    mandate: str
    #: What it will not do. Carried as prominently as the mandate.
    non_goals: list[str]
    trigger_conditions: list[str]
    permitted_callers: list[str]
    evidence_boundaries: list[str]
    human_gates: list[str]
    tools: list[CatalogueTool]
    read_only: bool
    budgets: CatalogueBudgets
    known_limitations: list[str]
    release: CatalogueRelease


class CatalogueDomain(BaseModel):
    domain: str
    agents: int


class CatalogueTotals(BaseModel):
    agents: int
    released: int
    directly_callable: int
    with_human_gates: int
    read_only: int


class AgentCatalogueResponse(BaseModel):
    agents: list[CatalogueAgent]
    totals: CatalogueTotals
    domains: list[CatalogueDomain]


class EconomicsEngagement(BaseModel):
    engagement_id: str
    code: str
    title: str
    status: str


class EconomicsMeasured(BaseModel):
    model_calls: int
    input_tokens: int
    output_tokens: int
    #: Elapsed time for the audit, including waiting on leases and on people.
    wall_clock_seconds: float
    #: Time the fleet spent generating. The gap to wall clock is what an
    #: asynchronous engagement buys back.
    agent_seconds: float
    tasks: int
    task_attempts: int
    population_records: int
    control_tests: int
    evidence_records: int
    human_decisions: int


class EconomicsModelUsage(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    #: False when the counts came from the scripted client, whose "tokens" are
    #: words. A surface that renders an unmetered figure without saying so is
    #: reporting arithmetic as a measurement.
    metered: bool


class EconomicsCost(BaseModel):
    priced_as: str
    price_basis: str
    input_usd_per_million: float
    output_usd_per_million: float
    usd: float
    introductory_usd: float
    introductory_note: str


class EconomicsComparison(BaseModel):
    annual_function_cost_usd: float
    headcount: int
    #: The comparison is only as good as this line, so it travels with it.
    assumption: str
    equivalent_runs: int | None = None
    #: The size of audit the quotient is drawn at. Without it "N audits" is a
    #: number divided by whatever this tenant happened to run.
    run_size_documents: int | None = None
    run_cost_usd: float | None = None


class EconomicsProjectionPoint(BaseModel):
    documents: int
    input_tokens: int
    output_tokens: int
    usd: float
    introductory_usd: float


class EconomicsProjectionInputs(BaseModel):
    mean_document_bytes: int
    tokens_per_document: int
    documents_measured: int
    output_tokens_per_model_call: int | None = None


class EconomicsProjection(BaseModel):
    """What an audit of a given size would cost, scaled from measured unit costs.

    Separate from ``cost``, which is a measurement, because the two answer
    different questions and a surface that renders them alike would let a
    projection borrow a measurement's authority.
    """

    priced_as: str
    points: list[EconomicsProjectionPoint]
    measured_inputs: EconomicsProjectionInputs
    assumptions: list[str]
    caveat: str


class EconomicsResponse(BaseModel):
    engagement: EconomicsEngagement | None
    #: ``programme`` (the whole tenant, the default) or ``engagement``.
    scope: str
    engagements: int
    measured: EconomicsMeasured
    models: list[EconomicsModelUsage]
    cost: EconomicsCost
    #: ``metered`` | ``scripted`` | ``mixed`` | ``none``.
    measurement: str
    #: The sentence a surface must print beside the number. Text rather than a
    #: flag so a caller cannot render the figure and drop the qualification.
    caveat: str | None = None
    comparison: EconomicsComparison
    projection: EconomicsProjection


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
    # Which guardrail screened it. An evaluator watching this action needs to know
    # whether the managed Google service was in the path or only the local checks.
    screened_by: str
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
