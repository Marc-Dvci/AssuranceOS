from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class EngagementStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class FailureClass(StrEnum):
    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"
    CONNECTOR_RATE_LIMIT = "connector_rate_limit"
    AUTHENTICATION_EXPIRED = "authentication_expired"
    SOURCE_SCHEMA_CHANGE = "source_schema_change"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_POLICY_VIOLATION = "model_policy_violation"
    MALFORMED_STRUCTURED_OUTPUT = "malformed_structured_output"
    DETERMINISTIC_TEST_FAILURE = "deterministic_test_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    HUMAN_RESPONSE_OVERDUE = "human_response_overdue"
    REVOKED_AUTHORIZATION = "revoked_authorization"
    SECURITY_INCIDENT = "security_incident"
    CONFIGURATION_ERROR = "configuration_error"
    INTERNAL_ERROR = "internal_error"
    LEASE_EXPIRED = "lease_expired"
    HUMAN_REJECTED = "human_rejected"


DEFAULT_RETRYABLE_FAILURES = frozenset(
    {
        FailureClass.TRANSIENT_INFRASTRUCTURE,
        FailureClass.CONNECTOR_RATE_LIMIT,
        FailureClass.AUTHENTICATION_EXPIRED,
        FailureClass.MODEL_TIMEOUT,
        FailureClass.MALFORMED_STRUCTURED_OUTPUT,
        FailureClass.INTERNAL_ERROR,
        FailureClass.LEASE_EXPIRED,
    }
)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=100)
    initial_delay_seconds: float = Field(default=5.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)
    max_delay_seconds: float = Field(default=300.0, ge=0)
    retryable_failures: set[FailureClass] = Field(
        default_factory=lambda: set(DEFAULT_RETRYABLE_FAILURES)
    )

    def delay_for_attempt(self, attempt_count: int) -> float:
        """Return delay after a failed attempt.

        ``attempt_count`` is one-based because a lease claim increments the persisted attempt
        count before execution begins.
        """
        exponent = max(attempt_count - 1, 0)
        return min(
            self.initial_delay_seconds * (self.backoff_multiplier**exponent),
            self.max_delay_seconds,
        )


class DependencyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_key: str = Field(min_length=1, max_length=128)
    allowed_statuses: set[TaskStatus] = Field(
        default_factory=lambda: {TaskStatus.SUCCEEDED, TaskStatus.SKIPPED}
    )

    @model_validator(mode="after")
    def terminal_statuses_only(self) -> "DependencyDefinition":
        allowed = {TaskStatus.SUCCEEDED, TaskStatus.SKIPPED}
        if not self.allowed_statuses or not self.allowed_statuses.issubset(allowed):
            raise ValueError("dependency allowed_statuses may contain only succeeded or skipped")
        return self


class TaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    task_type: str = Field(min_length=1, max_length=64)
    definition_version: str = Field(default="1", min_length=1, max_length=64)
    dependencies: list[DependencyDefinition] = Field(default_factory=list)
    assigned_agent_role: str | None = Field(default=None, max_length=128)
    input_refs: list[str] = Field(default_factory=list)
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    model_policy: str | None = Field(default=None, max_length=128)
    tool_policy: str | None = Field(default=None, max_length=128)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    priority: int = Field(default=100, ge=0, le=10000)
    deadline_seconds: int | None = Field(default=None, ge=1)
    human_gate: str | None = Field(default=None, max_length=128)
    human_gate_position: Literal["before", "after"] = "before"

    @model_validator(mode="after")
    def gate_position_requires_gate(self) -> "TaskDefinition":
        if self.human_gate is None and self.human_gate_position != "before":
            raise ValueError("human_gate_position is meaningful only when human_gate is set")
        return self

    def persisted_execution_policy(self) -> dict[str, Any]:
        policy = dict(self.execution_policy)
        policy["retry_policy"] = self.retry_policy.model_dump(mode="json")
        if self.human_gate:
            policy["human_gate_position"] = self.human_gate_position
        return policy


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_version: str = Field(min_length=1, max_length=64)
    tasks: list[TaskDefinition] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    engagement_id: str
    task_id: str
    task_key: str
    task_type: str
    assigned_agent_role: str | None = None
    attempt_count: int
    lease_owner: str
    lease_expires_at: datetime
    input_refs: list[str] = Field(default_factory=list)
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    model_policy: str | None = None
    tool_policy: str | None = None
    deadline_at: datetime | None = None
    human_gate: str | None = None


class TaskExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_refs: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4000)


class TaskSnapshot(BaseModel):
    task_id: str
    task_key: str
    status: TaskStatus
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    available_at: datetime | None
    human_gate: str | None
    review_status: str | None
    error_class: str | None
    output_refs: list[str] = Field(default_factory=list)


class TaskAttemptSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt_id: str
    task_id: str
    attempt_no: int
    worker_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    lease_expires_at: datetime | None
    failure_class: str | None
    error_message: str | None
    result_json: dict[str, Any] | None
    output_refs_json: list[str] = Field(default_factory=list)
    trace_id: str | None


class EngagementSnapshot(BaseModel):
    tenant_id: str
    engagement_id: str
    status: EngagementStatus
    tasks: list[TaskSnapshot]
