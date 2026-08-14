"""The governed agent run, and what it is attributable to afterwards.

The run itself was covered; what it left behind was not. It recorded its gateway
decisions with no engagement at all, so the delegation view — the one surface
that answers "what did the fleet actually do on this audit" — could not see the
only run in the tenant where an agent decided anything. The tests here pin the
attribution, and the adoption path that lets the run happen inside a compiled
plan instead of beside one.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from assuranceos.agent_audit_demo import (
    DEMO_ENGAGEMENT,
    DEMO_TASK,
    run_agent_audit_demo,
)
from assuranceos.db.models import (
    Engagement,
    EngagementTask,
    ExecutionTrace,
    GatewayDecisionRecord,
    Tenant,
)
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.delegation import engagement_delegation

ROOT = Path(__file__).resolve().parents[1]
TENANT = "tnt_agent_audit"
PLAN = "eng_compiled_plan"
PLAN_TASK = "tsk_execute_population_test"


@pytest.fixture
def database(tmp_path: Path):
    db = Database.from_sqlite_path(tmp_path / "agent-audit.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


def _seed_plan(database: Database, *, role: str = "operating-effectiveness") -> None:
    """A compiled engagement with a step already routed to the testing role."""
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id=TENANT, slug="a", name="Asteria", status="active")
        )
        session.flush()
        session.add(
            Engagement(
                engagement_id=PLAN,
                tenant_id=TENANT,
                code="SCM-2026-07",
                title="Software change management",
                status="planned",
                audit_pack_ref="software-change-management@2.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
        session.flush()
        session.add(
            EngagementTask(
                task_id=PLAN_TASK,
                tenant_id=TENANT,
                engagement_id=PLAN,
                task_key="execute-population-test",
                task_type="control_test",
                definition_version="1.0.0",
                status="pending",
                priority=103,
                assigned_agent_role=role,
                idempotency_key=f"{PLAN}:execute-population-test",
            )
        )


def _decisions(database: Database) -> list[GatewayDecisionRecord]:
    with database.read_session() as session:
        return list(session.scalars(select(GatewayDecisionRecord)))


def test_every_gateway_decision_carries_the_engagement_it_was_made_on(database):
    """Recording a decision with no engagement hides the run from the fleet view."""
    result = run_agent_audit_demo(
        database=database, repository_root=ROOT, tenant_id=TENANT
    )

    assert result["engagement_id"] == DEMO_ENGAGEMENT
    decisions = _decisions(database)
    assert decisions, "the run recorded no gateway decisions at all"
    assert {decision.engagement_id for decision in decisions} == {DEMO_ENGAGEMENT}
    assert {decision.task_id for decision in decisions} == {DEMO_TASK}

    with database.read_session() as session:
        traces = list(session.scalars(select(ExecutionTrace)))
    assert [trace.engagement_id for trace in traces] == [DEMO_ENGAGEMENT]

    view = engagement_delegation(database, TENANT)
    assert view["engagement"]["engagement_id"] == DEMO_ENGAGEMENT
    assert view["totals"]["gateway_allowed"] >= 1
    # The boundary probe: a tool outside the envelope, denied under the agent's
    # own identity. If this reads 0 the demonstration proved nothing.
    assert view["totals"]["gateway_denied"] == 1


def test_the_run_adopts_the_plan_step_instead_of_inventing_one(database):
    _seed_plan(database)

    result = run_agent_audit_demo(
        database=database,
        repository_root=ROOT,
        tenant_id=TENANT,
        engagement_id=PLAN,
        task_id=PLAN_TASK,
    )

    assert result["engagement_id"] == PLAN
    with database.read_session() as session:
        engagements = list(session.scalars(select(Engagement)))
        tasks = list(session.scalars(select(EngagementTask)))
    # No parallel engagement, no parallel task: the work landed on the plan.
    assert [item.engagement_id for item in engagements] == [PLAN]
    assert [task.task_id for task in tasks] == [PLAN_TASK]
    assert tasks[0].task_key == "execute-population-test"
    assert tasks[0].status == "running"
    # An engagement an agent is working inside is not "planned" any more.
    assert engagements[0].status == "fieldwork"

    assert {decision.engagement_id for decision in _decisions(database)} == {PLAN}
    view = engagement_delegation(database, TENANT)
    assert view["engagement"]["engagement_id"] == PLAN
    assert view["totals"]["gateway_denied"] == 1


def test_it_refuses_a_task_routed_to_another_role(database):
    """Adopting must not rewrite the plan's own routing to make itself fit."""
    _seed_plan(database, role="evidence-custodian")

    with pytest.raises(ValueError, match="evidence-custodian"):
        run_agent_audit_demo(
            database=database,
            repository_root=ROOT,
            tenant_id=TENANT,
            engagement_id=PLAN,
            task_id=PLAN_TASK,
        )
