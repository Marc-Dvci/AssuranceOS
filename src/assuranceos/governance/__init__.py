"""Security, governance, and telemetry for the AssuranceOS agent fleet.

Four subsystems, composed rather than layered ad hoc:

* :mod:`identity` — zero-trust workload identity. Short-lived Ed25519 credentials
  bound to one tenant, engagement, task, and attempt.
* :mod:`gateway` — the single enforcement point for every agent call. Routing,
  policy, separation of duties, human gates, and budgets.
* :mod:`armor` — inline guardrails against prompt injection, tool poisoning, and
  personal-data or secret leaks.
* :mod:`telemetry` — OpenTelemetry-compliant traces, audit log records, and
  reconstructable reasoning chains.
"""

from .armor import ArmorFinding, ArmorResult, ModelArmor
from .gateway import AgentGateway, BoundedTool, GatewayDecision, GatewayDenied, TaskBudget
from .identity import (
    AgentIdentity,
    AgentIdentityError,
    AgentIdentityIssuer,
    AgentIdentityVerifier,
    InMemoryRevocationList,
    SignedAgentIdentity,
    derive_granted_authority,
    generate_agent_identity_keypair,
    workload_uri,
)
from .telemetry import (
    AgentTracer,
    ReasoningChain,
    RecordedSpan,
    TelemetryConfig,
    audit_log_record,
    configure_telemetry,
    genai_attributes,
    new_span_id,
    new_trace_id,
    summarize_chains,
)

__all__ = [
    "AgentGateway",
    "AgentIdentity",
    "AgentIdentityError",
    "AgentIdentityIssuer",
    "AgentIdentityVerifier",
    "AgentTracer",
    "ArmorFinding",
    "ArmorResult",
    "BoundedTool",
    "GatewayDecision",
    "GatewayDenied",
    "InMemoryRevocationList",
    "ModelArmor",
    "ReasoningChain",
    "RecordedSpan",
    "SignedAgentIdentity",
    "TaskBudget",
    "TelemetryConfig",
    "audit_log_record",
    "configure_telemetry",
    "derive_granted_authority",
    "genai_attributes",
    "generate_agent_identity_keypair",
    "new_span_id",
    "new_trace_id",
    "summarize_chains",
    "workload_uri",
]
