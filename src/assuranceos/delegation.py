"""One engagement, as it was actually routed across the specialist fleet.

The fleet inventory answers "what agents exist". It does not answer the question
a judge, or an audit committee, actually asks: *who did what on this piece of
work, and what were they allowed to do while they did it?*

This read model answers that from canonical state alone. Nothing here is
declarative — every row is a task the orchestrator leased to a role, a decision
the gateway made, or a guardrail finding Model Armor recorded. An agent that is
released but never ran does not appear. An agent that ran and was denied appears
with the denial.

Two things it makes visible that a list of agents cannot:

* **The handoff.** Tasks in execution order, with the role each was assigned to,
  so a reader can see the work move from evidence collection to testing to
  contradiction search to the human gate.
* **Authority actually exercised.** For each role: the tools its signed package
  permits, the tools it in fact called, and every deny. The gap between the
  first two is the point — bounded authority is only meaningful if you can see
  that the bound was never reached.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from .db.models import (
    Engagement,
    EngagementTask,
    ExecutionTrace,
    Finding,
    GatewayDecisionRecord,
    GuardrailFindingRecord,
)
from .db.session import Database

#: Roles that never carry an agent. The orchestrator assigns some tasks to a
#: human or to a deterministic runtime, and rendering those as unused agents
#: would misreport both.
_NON_AGENT_ROLES = frozenset({"", "human", "system", "deterministic"})

#: Task states that mean the work has not been attempted yet.
_UNSTARTED = frozenset({"pending", "ready", "blocked"})


def engagement_delegation(
    database: Database,
    tenant_id: str,
    *,
    engagement_id: str | None = None,
    packages: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Who did what on one engagement, and under what authority.

    ``engagement_id`` defaults to the engagement with the most tasks, which is
    the one worth looking at. Passing it explicitly is how the cockpit links a
    specific audit to its routing.
    """
    packages = packages or {}
    with database.read_session() as session:
        engagements = list(
            session.scalars(
                select(Engagement).where(Engagement.tenant_id == tenant_id)
            )
        )
        tasks = list(
            session.scalars(
                select(EngagementTask).where(EngagementTask.tenant_id == tenant_id)
            )
        )
        decisions = list(
            session.scalars(
                select(GatewayDecisionRecord).where(
                    GatewayDecisionRecord.tenant_id == tenant_id
                )
            )
        )
        guardrails = list(
            session.scalars(
                select(GuardrailFindingRecord).where(
                    GuardrailFindingRecord.tenant_id == tenant_id
                )
            )
        )
        findings = list(
            session.scalars(select(Finding).where(Finding.tenant_id == tenant_id))
        )
        traces = list(
            session.scalars(
                select(ExecutionTrace).where(ExecutionTrace.tenant_id == tenant_id)
            )
        )

    selected = _select_engagement(engagements, tasks, engagement_id)
    if selected is None:
        return {
            "engagement": None,
            "agents": [],
            "handoff": [],
            "totals": _totals([], [], []),
        }

    scoped_tasks = sorted(
        (task for task in tasks if task.engagement_id == selected.engagement_id),
        # Priority is where the pack compiler writes the graph order (101, 102,
        # …), so it is the planned sequence even before anything has run.
        # created_at cannot stand in for it: a compiled engagement writes every
        # task in one transaction and they all share a timestamp.
        key=lambda task: (task.priority, task.created_at, task.task_key),
    )
    task_ids = {task.task_id for task in scoped_tasks}
    scoped_decisions = [
        decision
        for decision in decisions
        if decision.engagement_id == selected.engagement_id
        or decision.task_id in task_ids
    ]
    # Guardrail findings correlate on the trace, not on the decision. A finding
    # raised while screening *inbound context* — which is where a prompt
    # injection arrives — is recorded before any tool call exists, so it carries
    # no decision id. Joining on decisions silently hides exactly the detections
    # the fleet exists to make.
    task_by_trace = {
        trace.trace_id: trace.task_id for trace in traces if trace.task_id
    }
    scoped_guardrails = [
        finding
        for finding in guardrails
        if task_by_trace.get(finding.trace_id) in task_ids
    ]
    finding_titles = {
        finding.finding_id: finding.code
        for finding in findings
        if finding.engagement_id == selected.engagement_id
    }

    return {
        "engagement": {
            "engagement_id": selected.engagement_id,
            "code": selected.code,
            "title": selected.title,
            "status": selected.status,
            "audit_pack_ref": selected.audit_pack_ref,
            "finding_count": len(finding_titles),
        },
        "agents": _agents(
            scoped_tasks,
            scoped_decisions,
            scoped_guardrails,
            packages,
            task_by_trace=task_by_trace,
        ),
        "handoff": _handoff(scoped_tasks),
        "totals": _totals(scoped_tasks, scoped_decisions, scoped_guardrails),
    }


def _select_engagement(
    engagements: list[Engagement],
    tasks: list[EngagementTask],
    engagement_id: str | None,
) -> Engagement | None:
    """The engagement worth looking at, when the caller does not name one.

    Ranked on how many *distinct specialist roles* the work was split across
    rather than on task count, because that is the question this view answers. A
    twelve-task engagement handled end to end by one role demonstrates less
    delegation than a five-task one that moved across five.
    """
    if engagement_id is not None:
        return next(
            (item for item in engagements if item.engagement_id == engagement_id), None
        )
    if not engagements:
        return None
    counts = Counter(task.engagement_id for task in tasks)
    per_engagement: dict[str, set[str]] = {}
    for task in tasks:
        role = task.assigned_agent_role or ""
        if role.lower() in _NON_AGENT_ROLES:
            continue
        per_engagement.setdefault(task.engagement_id, set()).add(role)
    return max(
        engagements,
        key=lambda item: (
            len(per_engagement.get(item.engagement_id, ())),
            counts.get(item.engagement_id, 0),
            item.engagement_id,
        ),
    )


def _agents(
    tasks: list[EngagementTask],
    decisions: list[GatewayDecisionRecord],
    guardrails: list[GuardrailFindingRecord],
    packages: dict[str, Any],
    *,
    task_by_trace: dict[str, str | None],
) -> list[dict[str, Any]]:
    roles = [
        role
        for role in dict.fromkeys(
            (task.assigned_agent_role or "") for task in tasks
        )
        if role.lower() not in _NON_AGENT_ROLES
    ]
    task_ids_by_role = {
        role: {task.task_id for task in tasks if task.assigned_agent_role == role}
        for role in roles
    }

    rows: list[dict[str, Any]] = []
    for role in roles:
        role_tasks = [task for task in tasks if task.assigned_agent_role == role]
        role_decisions = [
            decision
            for decision in decisions
            if decision.agent_role == role or decision.task_id in task_ids_by_role[role]
        ]
        denials = [d for d in role_decisions if d.decision == "deny"]
        package = packages.get(role)
        permitted = _permitted_tools(package)
        called = sorted({decision.tool_name for decision in role_decisions if decision.tool_name})
        rows.append(
            {
                "agent_role": role,
                "display_name": _manifest(package, "display_name", role),
                "version": _manifest(package, "version"),
                "release_digest": (
                    (package.release or {}).get("package_sha256") if package else None
                ),
                "tasks": [
                    {
                        "task_key": task.task_key,
                        "task_type": task.task_type,
                        "status": task.status,
                        "attempts": task.attempt_count,
                        "human_gate": task.human_gate,
                    }
                    for task in role_tasks
                ],
                "task_count": len(role_tasks),
                # Distinguishes "granted authority it never needed" from "has
                # not started". Without it every unrun role reads as 0 tools of 5
                # used, which looks like a broken integration rather than a
                # compiled plan waiting on its first lease.
                "tasks_executed": sum(
                    1 for task in role_tasks if task.status not in _UNSTARTED
                ),
                "human_gates": _manifest(package, "human_gates", []) or [],
                "tools_permitted": permitted,
                "tools_called": called,
                # The headline number. A role that may call nine tools and called
                # two exercised a fraction of the authority it was granted, and
                # that is the claim bounded authority makes.
                "authority_exercised": (
                    f"{len(called)}/{len(permitted)}" if permitted else f"{len(called)}/—"
                ),
                "allowed": sum(1 for d in role_decisions if d.decision == "allow"),
                "denied": len(denials),
                "denial_reasons": sorted({d.reason for d in denials}),
                "guardrail_findings": [
                    {
                        "direction": finding.direction,
                        "detector": finding.detector,
                        "category": finding.category,
                        "severity": finding.severity,
                        "verdict": finding.verdict,
                    }
                    for finding in guardrails
                    if task_by_trace.get(finding.trace_id) in task_ids_by_role[role]
                ],
            }
        )
    return rows


def _handoff(tasks: list[EngagementTask]) -> list[dict[str, Any]]:
    """The work in execution order, with the role it was routed to."""
    return [
        {
            "step": index,
            "task_key": task.task_key,
            "task_type": task.task_type,
            "agent_role": task.assigned_agent_role or "orchestrator",
            "status": task.status,
            "human_gate": task.human_gate,
            "attempts": task.attempt_count,
            # A task that failed and was retried is part of the story of how the
            # routing recovered, so the error stays on the record.
            "last_error": task.last_error,
        }
        for index, task in enumerate(tasks, start=1)
    ]


def _totals(
    tasks: Iterable[EngagementTask],
    decisions: Iterable[GatewayDecisionRecord],
    guardrails: Iterable[GuardrailFindingRecord],
) -> dict[str, Any]:
    tasks = list(tasks)
    decisions = list(decisions)
    guardrails = list(guardrails)
    roles = {
        task.assigned_agent_role
        for task in tasks
        if (task.assigned_agent_role or "").lower() not in _NON_AGENT_ROLES
    }
    return {
        "specialist_agents": len(roles),
        "tasks": len(tasks),
        "human_gates": sum(1 for task in tasks if task.human_gate),
        "gateway_allowed": sum(1 for d in decisions if d.decision == "allow"),
        "gateway_denied": sum(1 for d in decisions if d.decision == "deny"),
        "guardrail_blocks": sum(1 for f in guardrails if f.verdict == "block"),
    }


def _manifest(package: Any, key: str, default: Any = None) -> Any:
    if package is None:
        return default
    return package.manifest.get(key, default)


def _permitted_tools(package: Any) -> list[str]:
    if package is None:
        return []
    tools = package.tools.get("tools", []) if package.tools else []
    return sorted(
        str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")
    )
