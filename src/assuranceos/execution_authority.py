from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .execution_security import (
    Ed25519ExecutionEnvelopeSigner,
    SignedExecutionEnvelope,
)
from .models import ExecutionEnvelope
from .orchestration import TaskLease
from .policy import PolicyGateway
from .registry import AgentPackage


@dataclass(frozen=True)
class ExecutionAuthority:
    """Compile a claimed task lease into short-lived, signed agent authority.

    Scope and tools must be explicitly present in the persisted task policy. The
    service never infers broad authority from the agent package. Package budgets
    are ceilings, and the signed envelope cannot outlive the worker lease.
    """

    packages: Mapping[str, AgentPackage]
    signer: Ed25519ExecutionEnvelopeSigner
    default_ttl: timedelta = timedelta(minutes=15)

    def issue(self, lease: TaskLease, *, now: datetime | None = None) -> SignedExecutionEnvelope:
        current = _utc(now or datetime.now(timezone.utc))
        if lease.assigned_agent_role is None:
            raise ValueError("task is not assigned to an agent role")
        package = self.packages.get(lease.assigned_agent_role)
        if package is None:
            raise ValueError(f"assigned agent package is unavailable: {lease.assigned_agent_role}")

        policy = lease.execution_policy
        purpose = _required_string(policy, "purpose")
        allowed_tools = _string_list(policy, "allowed_tools")
        allowed_evidence_scopes = _string_list(policy, "allowed_evidence_scopes")
        package_budgets = package.manifest.get("budgets", {})
        token_budget = _bounded_int(
            policy.get("token_budget", package_budgets.get("token_budget", 60_000)),
            ceiling=int(package_budgets.get("token_budget", 60_000)),
            name="token_budget",
        )
        cost_budget = _bounded_float(
            policy.get("cost_budget_usd", package_budgets.get("cost_budget_usd", 8.0)),
            ceiling=float(package_budgets.get("cost_budget_usd", 8.0)),
            name="cost_budget_usd",
        )
        if not lease.model_policy:
            raise ValueError("agent task must pin a model policy")

        deadline = min(
            value for value in (lease.deadline_at, lease.lease_expires_at) if value is not None
        )
        deadline = _utc(deadline)
        if deadline <= current:
            raise ValueError("task lease has expired")

        envelope = ExecutionEnvelope(
            task_id=lease.task_id,
            engagement_id=lease.engagement_id,
            tenant_id=lease.tenant_id,
            agent_role=package.agent_id,
            agent_version=str(package.manifest["version"]),
            purpose=purpose,
            allowed_evidence_scopes=allowed_evidence_scopes,
            allowed_tools=allowed_tools,
            forbidden_actions=list(package.policy.get("forbidden_actions", [])),
            model_policy=lease.model_policy,
            token_budget=token_budget,
            cost_budget_usd=cost_budget,
            deadline=deadline,
            output_schema=str(package.manifest["output_schema"]),
            human_gate=lease.human_gate,
            lease_owner=lease.lease_owner,
            attempt_count=lease.attempt_count,
        )
        decision = PolicyGateway().authorize(package, envelope)
        if not decision.allowed:
            raise ValueError(decision.reason)
        ttl = min(self.default_ttl, deadline - current)
        return self.signer.issue(envelope, ttl=ttl, now=current)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("execution-authority timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _required_string(policy: dict[str, object], key: str) -> str:
    value = policy.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task execution policy must define {key}")
    return value.strip()


def _string_list(policy: dict[str, object], key: str) -> list[str]:
    value = policy.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"task execution policy must define {key} as a string list")
    if len(value) != len(set(value)):
        raise ValueError(f"task execution policy {key} contains duplicates")
    return list(value)


def _bounded_int(value: object, *, ceiling: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if value > ceiling:
        raise ValueError(f"{name} exceeds the signed agent-package ceiling")
    return value


def _bounded_float(value: object, *, ceiling: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise ValueError(f"{name} must be positive")
    if float(value) > ceiling:
        raise ValueError(f"{name} exceeds the signed agent-package ceiling")
    return float(value)
