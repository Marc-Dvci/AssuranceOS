from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


ClaimType = Literal[
    "observed_fact",
    "computed_result",
    "management_assertion",
    "inference",
    "auditor_judgment",
    "unknown",
    "scope_limitation",
]

Conclusion = Literal[
    "effective",
    "partially_effective",
    "ineffective",
    "not_applicable",
    "not_tested",
    "insufficient_evidence",
    "population_incomplete",
    "source_unreliable",
    "test_failed_technically",
    "scope_limitation",
    "proposed",
    "blocked",
]


class EvidenceReference(BaseModel):
    evidence_id: str
    source_type: str
    source_locator: str
    sha256: str
    collected_at: datetime
    classification: str = "internal"
    accepted: bool = True


class ExecutionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: f"tsk_{uuid4().hex[:16]}")
    engagement_id: str
    tenant_id: str
    agent_role: str
    agent_version: str
    purpose: str
    allowed_evidence_scopes: list[str]
    allowed_tools: list[str]
    forbidden_actions: list[str]
    model_policy: str
    token_budget: int = 60_000
    cost_budget_usd: float = 8.0
    deadline: datetime | None = None
    output_schema: str = "assurance.agent_result.v1"
    human_gate: str | None = None
    trace_level: str = "full-metadata-redacted-content"
    lease_owner: str | None = None
    attempt_count: int = Field(default=1, ge=1)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(default_factory=lambda: f"res_{uuid4().hex[:16]}")
    task_id: str
    agent_role: str
    conclusion: Conclusion
    summary: str
    claim_type: ClaimType
    evidence_references: list[EvidenceReference] = []
    missing_evidence: list[str] = []
    contradictory_evidence: list[str] = []
    assumptions: list[str] = []
    confidence: float = Field(ge=0, le=1)
    recommended_next_action: str
    policy_checks: dict[str, bool]
    requires_human_approval: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = {}


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:16]}")
    event_type: str
    tenant_id: str
    engagement_id: str | None = None
    task_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = {}
