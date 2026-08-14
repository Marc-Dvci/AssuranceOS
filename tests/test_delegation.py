"""The delegation read model: who did the work, and under what authority.

This view had no test at all, and it was rendering the one engagement in the
tenant guaranteed to prove nothing — a compiled plan, which contains every role
in the pack and has by definition executed none of them. Every agent showed 0 of
5 tools used and the totals showed 0 allowed, 0 denied, on the screen whose whole
subject is authority exercised.

The tests here fix the ranking in place and pin the two attribution paths a
decision can arrive by, because both were live in the seeded tenant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from assuranceos.db.models import (
    Engagement,
    EngagementTask,
    ExecutionTrace,
    GatewayDecisionRecord,
    GuardrailFindingRecord,
    Tenant,
)
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.delegation import engagement_delegation

TENANT = "tnt_a"
PLANNED = "eng_plan"
EXECUTED = "eng_run"


def _task(
    task_id: str,
    engagement_id: str,
    *,
    role: str,
    status: str = "pending",
    priority: int = 100,
    key: str | None = None,
) -> EngagementTask:
    return EngagementTask(
        task_id=task_id,
        tenant_id=TENANT,
        engagement_id=engagement_id,
        task_key=key or task_id,
        task_type="agent",
        definition_version="1.0.0",
        status=status,
        priority=priority,
        assigned_agent_role=role,
        idempotency_key=f"{engagement_id}:{task_id}",
    )


def _decision(
    decision_id: str,
    *,
    decision: str,
    task_id: str,
    engagement_id: str | None,
    role: str = "operating-effectiveness",
    tool: str = "tests.execute",
    stage: str = "completed",
    trace_id: str = "t" * 32,
) -> GatewayDecisionRecord:
    return GatewayDecisionRecord(
        decision_row_id=f"row_{decision_id}",
        decision_id=decision_id,
        tenant_id=TENANT,
        decision=decision,
        stage=stage,
        reason="because the policy said so",
        agent_role=role,
        tool_name=tool,
        task_id=task_id,
        engagement_id=engagement_id,
        trace_id=trace_id,
        span_id="s" * 16,
        occurred_at=datetime.now(UTC),
    )


@pytest.fixture
def database(tmp_path: Path):
    db = Database.from_sqlite_path(tmp_path / "delegation.db")
    db.create_schema()
    with db.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id=TENANT, slug="a", name="Asteria", status="active")
        )
        session.flush()
        # The compiled plan: every role in the pack, nothing started.
        session.add(
            Engagement(
                engagement_id=PLANNED,
                tenant_id=TENANT,
                code="SCM-2026-07",
                title="Software change management",
                status="planned",
                audit_pack_ref="software-change-management@2.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
        # The engagement work actually ran on: fewer roles, real decisions.
        session.add(
            Engagement(
                engagement_id=EXECUTED,
                tenant_id=TENANT,
                code="SCM-2026-07-B",
                title="Software change management — control testing",
                status="fieldwork",
                audit_pack_ref="software-change-management@2.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
        session.flush()
        for index, role in enumerate(
            [
                "scope-materiality",
                "evidence-custodian",
                "operating-effectiveness",
                "skeptic",
                "finding-adjudicator",
                "quality-reviewer",
            ],
            start=101,
        ):
            session.add(_task(f"tsk_plan_{index}", PLANNED, role=role, priority=index))
        session.add(
            _task(
                "tsk_run_1",
                EXECUTED,
                role="operating-effectiveness",
                status="running",
                priority=101,
            )
        )
        session.add(
            _task(
                "tsk_run_2",
                EXECUTED,
                role="evidence-custodian",
                status="succeeded",
                priority=102,
            )
        )
    try:
        yield db
    finally:
        db.dispose()


def test_the_engagement_that_exercised_authority_beats_the_bigger_plan(database):
    """Six planned roles must not outrank two that actually called tools."""
    with database.transaction() as session:
        session.add(
            _decision("d1", decision="allow", task_id="tsk_run_1", engagement_id=EXECUTED)
        )
        session.add(
            _decision(
                "d2",
                decision="deny",
                task_id="tsk_run_1",
                engagement_id=EXECUTED,
                tool="connector.write",
                stage="policy",
            )
        )

    view = engagement_delegation(database, TENANT)

    assert view["engagement"]["engagement_id"] == EXECUTED
    assert view["totals"]["gateway_allowed"] == 1
    assert view["totals"]["gateway_denied"] == 1
    assert view["totals"]["specialist_agents"] == 2


def test_a_decision_with_no_engagement_is_attributed_through_its_task(database):
    """The agent-audit run recorded exactly this shape, and it vanished.

    A decision that names only the task it was made for still belongs to that
    task's engagement. Ranking on the engagement column alone made those runs
    invisible to the view — which then reported that the fleet had done nothing.
    """
    with database.transaction() as session:
        session.add(
            _decision("d1", decision="allow", task_id="tsk_run_1", engagement_id=None)
        )
        session.add(
            _decision(
                "d2",
                decision="deny",
                task_id="tsk_run_1",
                engagement_id=None,
                tool="connector.write",
                stage="policy",
            )
        )

    view = engagement_delegation(database, TENANT)

    assert view["engagement"]["engagement_id"] == EXECUTED
    assert view["totals"]["gateway_denied"] == 1
    tested = next(
        agent for agent in view["agents"] if agent["agent_role"] == "operating-effectiveness"
    )
    assert tested["tools_called"] == ["connector.write", "tests.execute"]
    assert tested["denial_reasons"] == ["because the policy said so"]


def test_naming_an_engagement_overrides_the_ranking(database):
    with database.transaction() as session:
        session.add(
            _decision("d1", decision="allow", task_id="tsk_run_1", engagement_id=EXECUTED)
        )

    view = engagement_delegation(database, TENANT, engagement_id=PLANNED)

    assert view["engagement"]["engagement_id"] == PLANNED
    assert view["totals"]["gateway_allowed"] == 0
    # A compiled plan is a legitimate thing to look at; it just is not the
    # default. Every role reads as granted-but-unstarted rather than as broken.
    assert [agent["tasks_executed"] for agent in view["agents"]] == [0] * 6


def test_with_nothing_run_anywhere_the_widest_plan_is_still_shown(database):
    """No execution in the tenant at all: fall back to breadth, not to nothing."""
    view = engagement_delegation(database, TENANT)

    assert view["engagement"]["engagement_id"] == PLANNED
    assert view["totals"]["specialist_agents"] == 6


def test_guardrail_findings_correlate_through_the_trace(database):
    """Inbound-context detections carry no decision id, only a trace."""
    trace_id = "c" * 32
    with database.transaction() as session:
        session.add(
            _decision(
                "d1",
                decision="allow",
                task_id="tsk_run_1",
                engagement_id=EXECUTED,
                trace_id=trace_id,
            )
        )
        session.add(
            ExecutionTrace(
                trace_row_id="trc_1",
                tenant_id=TENANT,
                trace_id=trace_id,
                engagement_id=EXECUTED,
                task_id="tsk_run_1",
                status="completed",
                started_at=datetime.now(UTC),
            )
        )
        session.add(
            GuardrailFindingRecord(
                finding_row_id="gfr_1",
                tenant_id=TENANT,
                decision_id=None,
                trace_id=trace_id,
                span_id="s" * 16,
                direction="inbound_context",
                verdict="block",
                detector="conclusion_forcing",
                category="prompt_injection",
                severity="critical",
                match_count=1,
                excerpt_digest="e" * 64,
                occurred_at=datetime.now(UTC),
            )
        )

    view = engagement_delegation(database, TENANT)

    assert view["totals"]["guardrail_blocks"] == 1
    tested = next(
        agent for agent in view["agents"] if agent["agent_role"] == "operating-effectiveness"
    )
    assert tested["guardrail_findings"][0]["detector"] == "conclusion_forcing"
