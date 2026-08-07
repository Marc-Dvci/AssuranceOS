from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from assuranceos.db import Database
from assuranceos.db.models import (
    AuditPlan,
    AuditSchedule,
    Engagement,
    EngagementTemplate,
    ScheduleCursor,
    ScheduleOccurrence,
    Tenant,
)
from assuranceos.db.repositories import EngagementRepository, TenantRepository
from assuranceos.orchestration import TaskDefinition, WorkflowDefinition
from assuranceos.scheduling import (
    AuditPeriodRule,
    AuditScheduler,
    OccurrenceDecision,
    OccurrenceStatus,
    PreflightContext,
)
from assuranceos.scheduling.periods import AuditPeriodCalculator


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "scheduler.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def clock():
    return MutableClock(datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc))


@pytest.fixture
def scheduler(database, clock):
    return AuditScheduler(database, clock=clock)


def workflow_json() -> dict:
    return WorkflowDefinition(
        workflow_version="1.0.0",
        tasks=[TaskDefinition(key="collect", task_type="evidence_collection")],
    ).model_dump(mode="json")


def seed_schedule(
    database: Database,
    *,
    schedule_id: str = "sch_1",
    effective_from: datetime = datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc),
    recurrence_rule: str = "FREQ=MONTHLY;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
    launch_mode: str = "automatic",
    missed_occurrence_policy: str = "launch_latest",
    blackout_policy: dict | None = None,
    preflight_policy: dict | None = None,
    max_concurrent_engagements: int = 1,
) -> None:
    with database.transaction() as session:
        if TenantRepository(session).get("tnt_a") is None:
            TenantRepository(session).add(
                Tenant(tenant_id="tnt_a", slug="tenant-a", name="Tenant A", status="active")
            )
        session.add(
            AuditPlan(
                plan_id=f"plan_{schedule_id}",
                tenant_id="tnt_a",
                name=f"Plan {schedule_id}",
                version=1,
                status="approved",
            )
        )
        session.add(
            EngagementTemplate(
                template_id=f"tpl_{schedule_id}",
                tenant_id="tnt_a",
                name=f"SCM audit {schedule_id}",
                version=1,
                status="released",
                audit_pack_ref="software-change-management@1.0.0",
                scope_json={"repositories": ["asteria/payments-api"]},
                workflow_definition_json=workflow_json(),
            )
        )
        session.flush()
        session.add(
            AuditSchedule(
                schedule_id=schedule_id,
                tenant_id="tnt_a",
                plan_id=f"plan_{schedule_id}",
                template_id=f"tpl_{schedule_id}",
                name=f"SCM schedule {schedule_id}",
                version=1,
                status="active",
                recurrence_rule=recurrence_rule,
                timezone="Europe/Paris",
                effective_from=effective_from,
                audit_period_rule_json={"kind": "calendar_months", "months": 1},
                blackout_policy_json=blackout_policy or {},
                preflight_policy_json=preflight_policy or {},
                launch_mode=launch_mode,
                missed_occurrence_policy=missed_occurrence_policy,
                max_concurrent_engagements=max_concurrent_engagements,
            )
        )


def passing_context() -> PreflightContext:
    return PreflightContext(
        connector_health={"github": "healthy"},
        available_budget_usd=10,
        available_competencies={"internal_auditor"},
    )


def test_automatic_schedule_launches_one_durable_engagement(scheduler, database):
    seed_schedule(
        database,
        preflight_policy={
            "required_connectors": ["github"],
            "estimated_cost_usd": 2,
            "required_competencies": ["internal_auditor"],
        },
    )

    summary = scheduler.evaluate_due(tenant_id="tnt_a", context=passing_context())

    assert summary.occurrences_created == 1
    assert summary.launched == 1
    occurrence = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")[0]
    assert occurrence.status == OccurrenceStatus.LAUNCHED
    assert occurrence.period_start == date(2026, 7, 1)
    assert occurrence.period_end == date(2026, 7, 31)
    with database.read_session() as session:
        engagement = EngagementRepository(session).get("tnt_a", occurrence.engagement_id)
        assert engagement is not None and engagement.status == "running"
        assert len(EngagementRepository(session).list_tasks("tnt_a", engagement.engagement_id)) == 1
        cursor = session.get(ScheduleCursor, "sch_1")
        assert cursor is not None and cursor.last_evaluated_at is not None


def test_evaluation_is_idempotent(scheduler, database):
    seed_schedule(database)
    first = scheduler.evaluate_due(tenant_id="tnt_a")
    second = scheduler.evaluate_due(tenant_id="tnt_a")

    assert first.occurrences_created == 1
    assert second.occurrences_created == 0
    occurrences = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")
    assert len(occurrences) == 1
    with database.read_session() as session:
        engagements = session.query(Engagement).filter_by(tenant_id="tnt_a").all()
        assert len(engagements) == 1


def test_preflight_blocks_closed_and_retries_when_context_improves(scheduler, database):
    seed_schedule(
        database,
        preflight_policy={"required_connectors": ["github"]},
    )
    blocked = scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")[0]
    assert blocked.blocked == 1
    assert occurrence.status == OccurrenceStatus.PREFLIGHT_BLOCKED
    assert "connector:github" in occurrence.decision_reason

    retried = scheduler.evaluate_due(
        tenant_id="tnt_a",
        context=PreflightContext(connector_health={"github": "healthy"}),
    )
    occurrence = scheduler.get_occurrence(
        tenant_id="tnt_a", occurrence_id=occurrence.occurrence_id
    )
    assert retried.launched == 1
    assert occurrence.status == OccurrenceStatus.LAUNCHED


def test_approval_mode_does_not_preflight_or_launch_before_decision(scheduler, database):
    seed_schedule(
        database,
        launch_mode="approval_required",
        preflight_policy={"required_connectors": ["github"]},
    )
    summary = scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")[0]
    assert summary.waiting_approval == 1
    assert occurrence.status == OccurrenceStatus.WAITING_APPROVAL
    assert occurrence.preflight_result_json == {}

    approved = scheduler.approve_occurrence(
        tenant_id="tnt_a",
        occurrence_id=occurrence.occurrence_id,
        decision=OccurrenceDecision(
            actor_id="usr_reviewer",
            reason="Approved after scope review.",
            preflight_context=PreflightContext(connector_health={"github": "healthy"}),
        ),
    )
    assert approved.status == OccurrenceStatus.LAUNCHED
    assert approved.engagement_id is not None


def test_launch_latest_records_missed_coverage(scheduler, database, clock):
    clock.value = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)
    seed_schedule(
        database,
        effective_from=datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc),
        recurrence_rule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
        missed_occurrence_policy="launch_latest",
    )
    summary = scheduler.evaluate_due(tenant_id="tnt_a")
    occurrences = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")

    assert summary.occurrences_created == 5
    assert len([row for row in occurrences if row.status == OccurrenceStatus.SKIPPED]) == 4
    assert len([row for row in occurrences if row.status == OccurrenceStatus.LAUNCHED]) == 1


def test_blackout_delay_preserves_nominal_due_and_launches_when_eligible(
    scheduler, database, clock
):
    seed_schedule(
        database,
        blackout_policy={
            "windows": [
                {
                    "start": "2026-08-06",
                    "end": "2026-08-07",
                    "behavior": "delay",
                    "reason": "period-close freeze",
                }
            ]
        },
    )
    scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")[0]
    assert occurrence.status == OccurrenceStatus.DEFERRED
    assert occurrence.nominal_due == datetime(2026, 8, 6, 7, 0)
    assert occurrence.eligible_at == datetime(2026, 8, 10, 7, 0)

    clock.value = datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc)
    summary = scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.get_occurrence(
        tenant_id="tnt_a", occurrence_id=occurrence.occurrence_id
    )
    assert summary.launched == 1
    assert occurrence.status == OccurrenceStatus.LAUNCHED


def test_overlap_and_concurrency_are_visible_preflight_failures(scheduler, database):
    seed_schedule(database, max_concurrent_engagements=1)
    with database.transaction() as session:
        session.add(
            Engagement(
                engagement_id="eng_existing",
                tenant_id="tnt_a",
                template_id="tpl_sch_1",
                code="EXISTING",
                title="Existing audit",
                status="running",
                audit_pack_ref="software-change-management@1.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
    summary = scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(tenant_id="tnt_a", schedule_id="sch_1")[0]
    failed_codes = {
        check["code"]
        for check in occurrence.preflight_result_json["checks"]
        if not check["passed"]
    }
    assert summary.blocked == 1
    assert {"concurrency_limit", "period_overlap"}.issubset(failed_codes)


def test_simulation_reflects_daylight_saving_changes(scheduler, database):
    seed_schedule(
        database,
        effective_from=datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc),
        recurrence_rule="FREQ=MONTHLY;COUNT=2;BYMONTHDAY=25;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
    )
    items = scheduler.simulate(
        tenant_id="tnt_a",
        schedule_id="sch_1",
        window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 31, tzinfo=timezone.utc),
    )
    assert [item.nominal_due.hour for item in items] == [7, 8]


def test_calendar_month_period_uses_last_completed_month():
    calculator = AuditPeriodCalculator()
    start, end = calculator.calculate(
        datetime(2026, 8, 6, 9, tzinfo=timezone.utc),
        AuditPeriodRule(kind="calendar_months", months=1, end_offset_days=1),
    )
    assert (start, end) == (date(2026, 7, 1), date(2026, 7, 31))


def test_approval_is_idempotent_after_launch(scheduler, database):
    seed_schedule(database, launch_mode="approval_required")
    scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(
        tenant_id="tnt_a", schedule_id="sch_1"
    )[0]
    decision = OccurrenceDecision(
        actor_id="usr_reviewer",
        reason="Approved after scope review.",
    )

    first = scheduler.approve_occurrence(
        tenant_id="tnt_a",
        occurrence_id=occurrence.occurrence_id,
        decision=decision,
    )
    second = scheduler.approve_occurrence(
        tenant_id="tnt_a",
        occurrence_id=occurrence.occurrence_id,
        decision=decision,
    )

    assert first.status == OccurrenceStatus.LAUNCHED
    assert second.status == OccurrenceStatus.LAUNCHED
    assert second.engagement_id == first.engagement_id
    with database.read_session() as session:
        engagements = session.query(Engagement).filter_by(tenant_id="tnt_a").all()
        assert len(engagements) == 1


def test_launch_failure_is_delayed_and_retried(
    scheduler, database, clock, monkeypatch
):
    seed_schedule(
        database,
        preflight_policy={"launch_retry_seconds": 30},
    )
    original_compile = scheduler.orchestrator.compile_workflow
    calls = 0

    def fail_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated worker interruption")
        return original_compile(**kwargs)

    monkeypatch.setattr(scheduler.orchestrator, "compile_workflow", fail_once)

    first = scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(
        tenant_id="tnt_a", schedule_id="sch_1"
    )[0]
    assert first.failures == 1
    assert occurrence.status == OccurrenceStatus.LAUNCH_FAILED
    assert occurrence.launch_attempts == 1
    assert occurrence.last_error == "simulated worker interruption"

    immediate = scheduler.evaluate_due(tenant_id="tnt_a")
    assert immediate.launched == 0
    assert calls == 1

    clock.advance(seconds=30)
    retried = scheduler.evaluate_due(tenant_id="tnt_a")
    recovered = scheduler.get_occurrence(
        tenant_id="tnt_a", occurrence_id=occurrence.occurrence_id
    )
    assert retried.launched == 1
    assert recovered.status == OccurrenceStatus.LAUNCHED
    assert recovered.launch_attempts == 2
    assert recovered.last_error is None


def test_stale_launching_occurrence_resumes_idempotently(
    scheduler, database, clock
):
    seed_schedule(database, launch_mode="approval_required")
    scheduler.evaluate_due(tenant_id="tnt_a")
    occurrence = scheduler.list_occurrences(
        tenant_id="tnt_a", schedule_id="sch_1"
    )[0]
    with database.transaction() as session:
        persisted = session.get(ScheduleOccurrence, occurrence.occurrence_id)
        assert persisted is not None
        persisted.status = OccurrenceStatus.LAUNCHING
        persisted.decision_by = "usr_reviewer"
        persisted.preflight_result_json = {"passed": True, "checks": []}
        persisted.eligible_at = clock.value

    summary = scheduler.evaluate_due(tenant_id="tnt_a")
    recovered = scheduler.get_occurrence(
        tenant_id="tnt_a", occurrence_id=occurrence.occurrence_id
    )
    assert summary.launched == 1
    assert recovered.status == OccurrenceStatus.LAUNCHED
    assert recovered.engagement_id is not None
