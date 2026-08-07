"""The governed agent runtime, mounted on the durable orchestration task path.

Without this adapter the governance layer is a library that something has to
remember to call, and a control nobody is obliged to invoke is not a control.
Registering this handler for a task type means every agent task the orchestrator
dispatches is executed through workload identity, the Agent Gateway, Model Armor,
and the reasoning-chain recorder, because there is no other way for the worker to
run one.

The execution envelope is built from the lease — canonical orchestration state —
and never from anything the model produced. That is the direction of authority
the whole system rests on: the control plane says what a task may do, and the
model only proposes within it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import ExecutionEnvelope
from ..orchestration.definitions import FailureClass, TaskExecutionResult, TaskLease
from ..orchestration.exceptions import PermanentTaskError, RetryableTaskError
from ..registry import AgentRegistry
from .gateway import AgentGateway
from .identity import AgentIdentityIssuer
from .models_client import ModelClient
from .persistence import GovernanceRecorder
from .runtime import AgentRunResult, EvidenceItem, GovernedAgentRuntime
from .telemetry import AgentTracer, TelemetryConfig

#: How a governed run's status maps onto the orchestrator's retry semantics.
#:
#: Only an unreachable model is worth retrying. A denial, an inadmissible
#: conclusion, or a truncated reply will reproduce exactly on the next attempt
#: with the same inputs, and retrying them burns budget while delaying the
#: operator signal. Truncation in particular is a configuration fault: the output
#: ceiling is too small for the deployed model, and no number of attempts fixes
#: that.
_FAILURE_CLASS: dict[str, tuple[bool, FailureClass]] = {
    "model_unavailable": (True, FailureClass.MODEL_TIMEOUT),
    "denied": (False, FailureClass.MODEL_POLICY_VIOLATION),
    "schema_invalid": (False, FailureClass.MALFORMED_STRUCTURED_OUTPUT),
    "model_truncated": (False, FailureClass.CONFIGURATION_ERROR),
}

EvidenceLoader = Callable[[TaskLease], Sequence[EvidenceItem]]
InstructionLoader = Callable[[TaskLease], str]


def envelope_from_lease(
    lease: TaskLease,
    *,
    agent_version: str,
    allowed_tools: Sequence[str],
    allowed_evidence_scopes: Sequence[str],
    forbidden_actions: Sequence[str],
    purpose: str,
    token_budget: int = 60_000,
    cost_budget_usd: float = 8.0,
) -> ExecutionEnvelope:
    """Derive a task's authority from canonical orchestration state.

    The lease is the control plane's statement of what this task is. Building the
    envelope here, from the lease, is what prevents a model from widening its own
    scope: there is no path by which model output reaches these fields.
    """
    if lease.assigned_agent_role is None:
        raise PermanentTaskError(
            f"task {lease.task_id!r} has no assigned agent role and cannot be "
            "executed by the governed runtime",
            failure_class=FailureClass.CONFIGURATION_ERROR,
        )
    return ExecutionEnvelope(
        task_id=lease.task_id,
        engagement_id=lease.engagement_id,
        tenant_id=lease.tenant_id,
        agent_role=lease.assigned_agent_role,
        agent_version=agent_version,
        purpose=purpose,
        allowed_evidence_scopes=list(allowed_evidence_scopes),
        allowed_tools=list(allowed_tools),
        forbidden_actions=list(forbidden_actions),
        model_policy=lease.model_policy or "flash",
        token_budget=token_budget,
        cost_budget_usd=cost_budget_usd,
        deadline=lease.deadline_at,
        human_gate=lease.human_gate,
        lease_owner=lease.lease_owner,
        attempt_count=lease.attempt_count,
    )


class GovernedAgentTaskHandler:
    """Runs one orchestrated task through the full governed path.

    Registered against a task type in :class:`~assuranceos.orchestration.worker.LocalWorker`,
    this is the only way an agent task executes, so the enforcement point cannot
    be bypassed by a caller that forgets to use it.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        gateway: AgentGateway,
        identity_issuer: AgentIdentityIssuer,
        model_client: ModelClient,
        recorder: GovernanceRecorder | None = None,
        evidence_loader: EvidenceLoader | None = None,
        instruction_loader: InstructionLoader | None = None,
        telemetry: TelemetryConfig | None = None,
        max_output_tokens: int = 4096,
        envelope_ttl: timedelta = timedelta(minutes=15),
    ):
        self.registry = registry
        self.gateway = gateway
        self.identity_issuer = identity_issuer
        self.model_client = model_client
        self.recorder = recorder
        self.evidence_loader = evidence_loader or (lambda _: ())
        self.instruction_loader = instruction_loader or (
            lambda lease: f"Execute task {lease.task_key} for engagement {lease.engagement_id}."
        )
        self.telemetry = telemetry or TelemetryConfig()
        self.max_output_tokens = max_output_tokens
        self.envelope_ttl = envelope_ttl

    def __call__(self, lease: TaskLease) -> TaskExecutionResult:
        packages = self.registry.load()
        role = lease.assigned_agent_role
        package = packages.get(role) if role else None
        if package is None:
            raise PermanentTaskError(
                f"no released agent package for role {role!r}",
                failure_class=FailureClass.CONFIGURATION_ERROR,
            )

        policy = package.policy or {}
        envelope = envelope_from_lease(
            lease,
            agent_version=str(package.manifest["version"]),
            # The envelope grants what the package declares; the identity issuer
            # narrows it further to the package/envelope intersection. Widening
            # here would be silently undone there, which is the intended order.
            allowed_tools=[
                str(item.get("name"))
                for item in (package.tools or {}).get("tools", [])
                if item.get("name")
            ],
            allowed_evidence_scopes=list(package.manifest.get("evidence_boundaries", [])),
            forbidden_actions=list(policy.get("forbidden_actions", [])),
            purpose=str(package.manifest.get("mandate", lease.task_type)),
            token_budget=int((package.manifest.get("budgets") or {}).get("token_budget", 60_000)),
            cost_budget_usd=float(
                (package.manifest.get("budgets") or {}).get("cost_budget_usd", 8.0)
            ),
        )
        if envelope.deadline is None:
            envelope = envelope.model_copy(
                update={"deadline": datetime.now(timezone.utc) + self.envelope_ttl}
            )

        runtime = GovernedAgentRuntime(
            gateway=self.gateway,
            identity_issuer=self.identity_issuer,
            model_client=self.model_client,
            armor=self.gateway.armor,
            telemetry=self.telemetry,
            max_output_tokens=self.max_output_tokens,
        )
        tracer = AgentTracer(self.telemetry)

        # Decisions accumulate on the gateway across calls, so only the ones this
        # task produced are persisted against it.
        decision_mark = len(self.gateway.decisions)
        audit_mark = len(self.gateway.audit_events)

        result = runtime.run(
            package=package,
            envelope=envelope,
            instruction=self.instruction_loader(lease),
            evidence=self.evidence_loader(lease),
            tracer=tracer,
        )

        self._persist(lease, tracer, result, decision_mark, audit_mark)

        if not result.succeeded:
            self._raise_for(result)

        return TaskExecutionResult(
            output_refs=[f"agent-run:{result.trace_id}"],
            result=result.as_dict(),
        )

    # -- internals -------------------------------------------------------------

    def _persist(
        self,
        lease: TaskLease,
        tracer: AgentTracer,
        result: AgentRunResult,
        decision_mark: int,
        audit_mark: int,
    ) -> None:
        """Record the run even when it failed.

        A denied or truncated run is precisely the one an auditor needs to be able
        to reconstruct, so persistence happens before the failure is raised rather
        than on the success path only.
        """
        if self.recorder is None:
            return
        self.recorder.record_decisions(
            self.gateway.decisions[decision_mark:],
            audit_events=self.gateway.audit_events[audit_mark:],
            engagement_id=lease.engagement_id,
        )
        self.recorder.record_chain(
            tracer.chain,
            tenant_id=lease.tenant_id,
            engagement_id=lease.engagement_id,
            task_id=lease.task_id,
            agent_role=lease.assigned_agent_role,
        )

    @staticmethod
    def _raise_for(result: AgentRunResult) -> None:
        retryable, failure_class = _FAILURE_CLASS.get(
            result.status, (False, FailureClass.INTERNAL_ERROR)
        )
        message = f"governed run {result.status}: {result.summary}"
        if result.denials:
            message += f" (denials: {'; '.join(result.denials[:3])})"
        error = RetryableTaskError if retryable else PermanentTaskError
        raise error(message, failure_class=failure_class)


def evidence_from_records(records: Sequence[dict[str, Any]]) -> list[EvidenceItem]:
    """Adapt canonical evidence rows to the runtime's context items.

    Evidence collected from an external source is marked tainted, which is what
    routes it through the stricter inbound guardrail. Defaulting to tainted means
    a source that forgets to declare itself is treated as untrusted.
    """
    return [
        EvidenceItem(
            evidence_id=str(record["evidence_id"]),
            source_type=str(record.get("source_type", "unknown")),
            content=str(record.get("content", "")),
            tainted=bool(record.get("tainted", True)),
        )
        for record in records
    ]
