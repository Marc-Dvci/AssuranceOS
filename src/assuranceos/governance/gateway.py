"""Agent Gateway — unified routing and policy enforcement.

Every call an agent makes passes through here. There is no second path to a tool,
which is what makes the guarantees checkable: to reason about what the fleet can
do, you read this file and the signed packages, not each agent's code.

The gateway is the policy *enforcement* point. ``PolicyGateway`` remains the
policy *decision* point for released packages, and this module composes it with
workload identity, inline guardrails, budgets, and tracing. Splitting the two
keeps package semantics testable on their own.

Enforcement is ordered cheapest-first and fails closed at every step:

1. authenticate the workload identity (signature, lifetime, revocation);
2. verify the execution envelope (signature, lifetime, task deadline);
3. bind the two together — a credential is valid for one invocation only;
4. evaluate the released package policy;
5. resolve routing, where an unregistered tool is denied by default;
6. enforce separation of duties;
7. enforce the human gate;
8. enforce token, cost, and call budgets;
9. screen proposed arguments with Model Armor;
10. invoke the bounded handler;
11. screen the result with Model Armor.

A denial at any step produces an attributable record and never reaches the tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import uuid4

from ..models import AuditEvent, ExecutionEnvelope
from ..policy import PolicyGateway
from ..registry import AgentPackage
from .armor import ArmorResult, ModelArmor
from .identity import AgentIdentity, AgentIdentityError, AgentIdentityVerifier
from .telemetry import (
    SPAN_ARMOR,
    SPAN_IDENTITY,
    SPAN_POLICY,
    SPAN_TOOL,
    AgentTracer,
    audit_log_record,
)


class GatewayDenied(Exception):
    """Raised when the gateway refuses a call. Carries the attributable decision."""

    def __init__(self, decision: "GatewayDecision"):
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True)
class GatewayDecision:
    decision_id: str
    decision: str  # "allow" | "deny"
    stage: str
    reason: str
    tenant_id: str
    agent_role: str
    tool_name: str
    task_id: str
    trace_id: str
    span_id: str
    occurred_at: datetime
    identity_id: str | None = None
    armor: tuple[ArmorResult, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision": self.decision,
            "stage": self.stage,
            "reason": self.reason,
            "tenant_id": self.tenant_id,
            "agent_role": self.agent_role,
            "tool_name": self.tool_name,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "identity_id": self.identity_id,
            "occurred_at": self.occurred_at.isoformat(),
            "armor": [result.as_dict() for result in self.armor],
            "attributes": dict(self.attributes),
        }


class ToolHandler(Protocol):
    def __call__(
        self,
        *,
        arguments: Mapping[str, Any],
        identity: AgentIdentity,
        envelope: ExecutionEnvelope,
    ) -> Any: ...


@dataclass(frozen=True)
class BoundedTool:
    """A concrete implementation bound to a tool declared in a signed package."""

    name: str
    handler: ToolHandler
    description: str = ""

    @property
    def qualified_name(self) -> str:
        return self.name


@dataclass
class TaskBudget:
    """Per-task consumption ceiling, enforced before a tool is reached."""

    max_calls: int = 50
    token_budget: int = 60_000
    cost_budget_usd: float = 8.0
    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0

    @classmethod
    def from_envelope(cls, envelope: ExecutionEnvelope, *, max_calls: int = 50) -> "TaskBudget":
        return cls(
            max_calls=max_calls,
            token_budget=envelope.token_budget,
            cost_budget_usd=envelope.cost_budget_usd,
        )

    def exceeded(self) -> str | None:
        if self.calls >= self.max_calls:
            return f"call budget exhausted ({self.calls}/{self.max_calls})"
        if self.tokens > self.token_budget:
            return f"token budget exhausted ({self.tokens}/{self.token_budget})"
        if self.cost_usd > self.cost_budget_usd:
            return f"cost budget exhausted ({self.cost_usd:.4f}/{self.cost_budget_usd:.4f} USD)"
        return None

    def consume(self, *, tokens: int = 0, cost_usd: float = 0.0) -> None:
        self.calls += 1
        self.tokens += tokens
        self.cost_usd += cost_usd


class AgentGateway:
    """The single enforcement point between an agent and everything it can reach."""

    def __init__(
        self,
        *,
        identity_verifier: AgentIdentityVerifier,
        policy_gateway: PolicyGateway | None = None,
        armor: ModelArmor | None = None,
        clock: Callable[[], datetime] | None = None,
        max_calls_per_task: int = 50,
    ):
        self.identity_verifier = identity_verifier
        self.policy_gateway = policy_gateway or PolicyGateway()
        self.armor = armor or ModelArmor()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_calls_per_task = max_calls_per_task
        self._routes: dict[tuple[str, str], BoundedTool] = {}
        self._budgets: dict[str, TaskBudget] = {}
        self.decisions: list[GatewayDecision] = []
        self.audit_events: list[AuditEvent] = []
        self.audit_logs: list[dict[str, Any]] = []

    # -- routing ---------------------------------------------------------------

    def register_tool(self, agent_role: str, tool: BoundedTool) -> None:
        """Bind a concrete handler to a declared tool. Unregistered tools stay denied."""
        key = (agent_role, tool.name)
        if key in self._routes:
            raise ValueError(f"tool {tool.name!r} is already registered for {agent_role!r}")
        self._routes[key] = tool

    def registered_tools(self, agent_role: str) -> list[str]:
        return sorted(name for role, name in self._routes if role == agent_role)

    def budget_for(self, task_id: str) -> TaskBudget | None:
        return self._budgets.get(task_id)

    # -- enforcement -----------------------------------------------------------

    def invoke(
        self,
        *,
        signed_identity: Any,
        envelope: ExecutionEnvelope,
        package: AgentPackage,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        tracer: AgentTracer | None = None,
        estimated_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
    ) -> Any:
        """Authenticate, authorise, screen, and route one tool call.

        Returns the handler result. Raises :class:`GatewayDenied` otherwise.
        """
        arguments = dict(arguments or {})
        tracer = tracer or AgentTracer()
        armor_results: list[ArmorResult] = []

        with tracer.span(
            SPAN_TOOL,
            **{
                "assuranceos.tenant_id": envelope.tenant_id,
                "assuranceos.engagement_id": envelope.engagement_id,
                "assuranceos.task_id": envelope.task_id,
                "assuranceos.agent_role": envelope.agent_role,
                "assuranceos.agent_version": envelope.agent_version,
                "assuranceos.tool_name": tool_name,
            },
        ) as call_span:

            # Bound once authentication succeeds; a denial before that records no identity.
            current_identity: AgentIdentity | None = None

            def deny(stage: str, reason: str, **attributes: Any) -> GatewayDenied:
                tracer.deny(reason)
                decision = self._record(
                    decision="deny",
                    stage=stage,
                    reason=reason,
                    envelope=envelope,
                    tool_name=tool_name,
                    trace_id=tracer.trace_id,
                    span_id=call_span.span_id,
                    identity_id=current_identity.identity_id if current_identity else None,
                    armor=tuple(armor_results),
                    attributes=attributes,
                )
                return GatewayDenied(decision)

            # 1-3. Authenticate the workload and bind it to this exact invocation.
            with tracer.span(SPAN_IDENTITY):
                try:
                    identity = self.identity_verifier.verify(
                        signed_identity, envelope=envelope, now=self.clock()
                    )
                except AgentIdentityError as exc:
                    raise deny("identity", str(exc)) from exc
                current_identity = identity
                tracer.allow()
                call_span.attributes["assuranceos.identity_id"] = identity.identity_id
                call_span.attributes["assuranceos.workload_uri"] = identity.workload_uri

            # 4. Released package policy.
            with tracer.span(SPAN_POLICY):
                package_decision = self.policy_gateway.authorize_tool(
                    package, envelope, tool_name
                )
                if not package_decision.allowed:
                    raise deny(
                        "policy",
                        package_decision.reason,
                        denied_tools=list(package_decision.denied_tools),
                    )

                # 5. Routing. A declared tool with no bound handler is still denied.
                route = self._routes.get((envelope.agent_role, tool_name))
                if route is None:
                    raise deny(
                        "routing",
                        f"tool {tool_name!r} has no bound handler for {envelope.agent_role!r}",
                    )

                # The identity's granted set is the package/envelope intersection.
                if tool_name not in identity.granted_tools:
                    raise deny(
                        "policy",
                        f"tool {tool_name!r} is outside the granted authority of this identity",
                    )

                # 6. Separation of duties.
                if breach := self._independence_breach(identity, arguments):
                    raise deny("independence", breach)

                # 7. Human gate.
                declared = self._declared_tool(package, tool_name)
                if declared.get("requires_human_confirmation") and not envelope.human_gate:
                    raise deny(
                        "human_gate",
                        f"tool {tool_name!r} requires a human gate that the envelope does not carry",
                    )
                tracer.allow()

            # 8. Budgets.
            budget = self._budgets.setdefault(
                envelope.task_id,
                TaskBudget.from_envelope(envelope, max_calls=self.max_calls_per_task),
            )
            if exhausted := budget.exceeded():
                raise deny("budget", exhausted, calls=budget.calls, tokens=budget.tokens)

            # 9. Screen proposed arguments.
            with tracer.span(SPAN_ARMOR, **{"assuranceos.armor.direction": "tool_call"}):
                argument_armor = self.armor.inspect_tool_call(
                    tool_name,
                    arguments,
                    granted_evidence_scopes=identity.granted_evidence_scopes,
                    forbidden_actions=identity.forbidden_actions,
                )
                armor_results.append(argument_armor)
                if argument_armor.blocked:
                    detail = "; ".join(f.detail for f in argument_armor.findings[:3])
                    raise deny(
                        "model_armor",
                        f"tool arguments failed inline guardrails: {detail}",
                        findings=[f.as_dict() for f in argument_armor.findings],
                    )
                tracer.allow()

            # 10. Invoke the bounded handler.
            budget.consume(tokens=estimated_tokens, cost_usd=estimated_cost_usd)
            try:
                result = route.handler(
                    arguments=arguments, identity=identity, envelope=envelope
                )
            except Exception as exc:
                raise deny("tool_execution", f"{type(exc).__name__}: {exc}") from exc

            # 11. Screen what comes back out.
            if isinstance(result, str):
                with tracer.span(SPAN_ARMOR, **{"assuranceos.armor.direction": "outbound_text"}):
                    output_armor = self.armor.inspect_output(result)
                    armor_results.append(output_armor)
                    if output_armor.blocked:
                        raise deny(
                            "model_armor",
                            "tool output withheld: secret material detected",
                            findings=[f.as_dict() for f in output_armor.findings],
                        )
                    if output_armor.redaction_count:
                        result = output_armor.sanitized_text
                    tracer.allow()

            tracer.allow()
            self._record(
                decision="allow",
                stage="completed",
                reason="allowed by identity, package policy, guardrails, and budget",
                envelope=envelope,
                tool_name=tool_name,
                trace_id=tracer.trace_id,
                span_id=call_span.span_id,
                identity_id=identity.identity_id,
                armor=tuple(armor_results),
                attributes={"calls": budget.calls, "tokens": budget.tokens},
            )
            return result

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _declared_tool(package: AgentPackage, tool_name: str) -> dict[str, Any]:
        for item in package.tools.get("tools", []):
            if item.get("name") == tool_name:
                return item
        return {}

    @staticmethod
    def _independence_breach(
        identity: AgentIdentity, arguments: Mapping[str, Any]
    ) -> str | None:
        """Refuse work the acting subject produced itself.

        Independent retest is only meaningful when the retester is not the author,
        so the constraint is enforced here rather than trusted to the caller.
        """
        if not identity.independence_constraints:
            return None
        subject = identity.independence_subject
        for constraint in identity.independence_constraints:
            candidate = arguments.get(constraint)
            if candidate is not None and subject is not None and str(candidate) == str(subject):
                return (
                    f"separation of duties: identity may not act on {constraint}="
                    f"{candidate!r}, which its own subject produced"
                )
        return None

    def _record(
        self,
        *,
        decision: str,
        stage: str,
        reason: str,
        envelope: ExecutionEnvelope,
        tool_name: str,
        trace_id: str,
        span_id: str,
        identity_id: str | None,
        armor: tuple[ArmorResult, ...],
        attributes: Mapping[str, Any],
    ) -> GatewayDecision:
        record = GatewayDecision(
            decision_id=f"gwd_{uuid4().hex[:16]}",
            decision=decision,
            stage=stage,
            reason=reason,
            tenant_id=envelope.tenant_id,
            agent_role=envelope.agent_role,
            tool_name=tool_name,
            task_id=envelope.task_id,
            trace_id=trace_id,
            span_id=span_id,
            occurred_at=self.clock(),
            identity_id=identity_id,
            armor=armor,
            attributes=dict(attributes),
        )
        self.decisions.append(record)
        self.audit_events.append(
            AuditEvent(
                event_type=f"agent.gateway.{decision}",
                tenant_id=envelope.tenant_id,
                engagement_id=envelope.engagement_id,
                task_id=envelope.task_id,
                occurred_at=record.occurred_at,
                payload=record.as_dict(),
            )
        )
        self.audit_logs.append(
            audit_log_record(
                trace_id=trace_id,
                span_id=span_id,
                tenant_id=envelope.tenant_id,
                actor=identity_id or envelope.agent_role,
                action=f"tool.{tool_name}",
                outcome=decision,
                extra={"assuranceos.stage": stage, "assuranceos.reason": reason},
            )
        )
        return record


