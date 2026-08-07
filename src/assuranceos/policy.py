from __future__ import annotations

from dataclasses import dataclass

from .models import ExecutionEnvelope
from .registry import AgentPackage


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    denied_tools: tuple[str, ...] = ()


class PolicyGateway:
    """Validates model-requested authority against the signed execution envelope."""

    def authorize(self, package: AgentPackage, envelope: ExecutionEnvelope) -> PolicyDecision:
        if envelope.agent_role != package.agent_id:
            return PolicyDecision(False, "agent identity does not match package")
        if envelope.agent_version != str(package.manifest["version"]):
            return PolicyDecision(False, "agent version does not match released package")

        declared = {item["name"] for item in package.tools.get("tools", [])}
        requested = set(envelope.allowed_tools)
        denied = tuple(sorted(requested - declared))
        if denied:
            return PolicyDecision(False, "execution envelope requests undeclared tools", denied)

        package_forbidden = set(package.policy.get("forbidden_actions", []))
        envelope_forbidden = set(envelope.forbidden_actions)
        if not envelope_forbidden.issuperset(package_forbidden):
            return PolicyDecision(False, "execution envelope weakens package prohibitions")

        return PolicyDecision(True, "allowed by package and execution envelope")

    def authorize_tool(self, package: AgentPackage, envelope: ExecutionEnvelope, tool_name: str) -> PolicyDecision:
        base = self.authorize(package, envelope)
        if not base.allowed:
            return base
        if tool_name not in envelope.allowed_tools:
            return PolicyDecision(False, f"tool {tool_name} absent from execution envelope")
        tool = next((t for t in package.tools.get("tools", []) if t["name"] == tool_name), None)
        if not tool:
            return PolicyDecision(False, f"tool {tool_name} absent from package")
        if tool.get("side_effect") != "none" and tool.get("requires_human_confirmation", False):
            if not envelope.human_gate:
                return PolicyDecision(False, f"tool {tool_name} requires a human gate")
        return PolicyDecision(True, "tool call allowed")
