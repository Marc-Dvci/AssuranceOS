from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from assuranceos.db import Database
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, TenantRepository
from assuranceos.orchestration import (
    DependencyDefinition,
    EngagementStatus,
    FailureClass,
    GateDecision,
    LeaseConflictError,
    LocalWorker,
    RetryPolicy,
    RetryableTaskError,
    TaskDefinition,
    TaskExecutionResult,
    TaskStatus,
    WorkflowDefinition,
    WorkflowValidationError,
    verify_replay,
)
from assuranceos.orchestration.compiler import WorkflowCompiler
from assuranceos.orchestration.service import Orchestrator


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def database(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "orchestration.db")
    database.create_schema()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def clock():
    return MutableClock(datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def orchestrator(database, clock):
    return Orchestrator(database, clock=clock)


def seed_engagement(database, *, tenant_id="tnt_a", engagement_id="eng_1"):
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id=tenant_id, slug=tenant_id, name=f"Tenant {tenant_id}")
        )
        session.add(
            Engagement(
                engagement_id=engagement_id,
                tenant_id=tenant_id,
                code=f"AUDIT-{engagement_id}",
                title="Test engagement",
                audit_pack_ref="test-pack@1",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )


def linear_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_version="1.0.0",
        tasks=[
            TaskDefinition(key="collect", task_type="collect"),
            TaskDefinition(
                key="test",
                task_type="test",
                dependencies=[DependencyDefinition(task_key="collect")],
            ),
            TaskDefinition(
                key="report",
                task_type="report",
                dependencies=[DependencyDefinition(task_key="test")],
            ),
        ],
    )


def by_key(snapshot):
    return {task.task_key: task for task in snapshot.tasks}


def test_compiler_rejects_unknown_dependency_and_cycles():
    compiler = WorkflowCompiler()
    with pytest.raises(WorkflowValidationError, match="unknown dependencies"):
        compiler.validate(
            WorkflowDefinition(
                workflow_version="1",
                tasks=[
                    TaskDefinition(
                        key="a",
                        task_type="test",
                        dependencies=[DependencyDefinition(task_key="missing")],
                    )
                ],
            )
        )

    with pytest.raises(WorkflowValidationError, match="dependency cycle"):
        compiler.validate(
            WorkflowDefinition(
                workflow_version="1",
                tasks=[
                    TaskDefinition(
                        key="a",
                        task_type="test",
                        dependencies=[DependencyDefinition(task_key="b")],
                    ),
                    TaskDefinition(
                        key="b",
                        task_type="test",
                        dependencies=[DependencyDefinition(task_key="a")],
                    ),
                ],
            )
        )


def test_start_promotes_only_dependency_free_tasks(orchestrator, database):
    seed_engagement(database)
    compiled = orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    assert compiled.status == EngagementStatus.PLANNED
    assert {task.status for task in compiled.tasks} == {TaskStatus.PENDING}

    started = orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    tasks = by_key(started)
    assert started.status == EngagementStatus.RUNNING
    assert tasks["collect"].status == TaskStatus.READY
    assert tasks["test"].status == TaskStatus.PENDING
    assert tasks["report"].status == TaskStatus.PENDING


def test_claim_is_exclusive_and_heartbeat_requires_owner(orchestrator, database, clock):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")

    lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    assert lease.task_key == "collect"
    assert lease.attempt_count == 1
    assert orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker-b") is None

    with pytest.raises(LeaseConflictError):
        orchestrator.heartbeat(
            tenant_id="tnt_a", task_id=lease.task_id, worker_id="worker-b"
        )
    clock.advance(seconds=5)
    extended = orchestrator.heartbeat(
        tenant_id="tnt_a", task_id=lease.task_id, worker_id="worker-a", lease_seconds=60
    )
    assert extended.lease_expires_at > lease.lease_expires_at


def test_completion_unlocks_dependencies_and_completes_engagement(orchestrator, database):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")

    for key in ["collect", "test", "report"]:
        lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
        assert lease is not None and lease.task_key == key
        snapshot = orchestrator.complete_task(
            tenant_id="tnt_a",
            task_id=lease.task_id,
            worker_id="worker",
            result=TaskExecutionResult(
                output_refs=[f"evidence:{key}"], result={"task": key, "ok": True}
            ),
        )

    assert snapshot.status == EngagementStatus.COMPLETED
    assert all(task.status == TaskStatus.SUCCEEDED for task in snapshot.tasks)
    assert by_key(snapshot)["report"].output_refs == ["evidence:report"]


def test_retryable_failure_uses_bounded_backoff(orchestrator, database, clock):
    seed_engagement(database)
    workflow = WorkflowDefinition(
        workflow_version="1",
        tasks=[
            TaskDefinition(
                key="sync",
                task_type="connector",
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    initial_delay_seconds=10,
                    backoff_multiplier=2,
                    max_delay_seconds=30,
                ),
            )
        ],
    )
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=workflow
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
    assert lease is not None

    snapshot = orchestrator.fail_task(
        tenant_id="tnt_a",
        task_id=lease.task_id,
        worker_id="worker",
        failure_class=FailureClass.CONNECTOR_RATE_LIMIT,
        message="rate limited",
    )
    task = snapshot.tasks[0]
    assert task.status == TaskStatus.RETRY_WAIT
    assert task.available_at is not None
    assert orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker") is None

    clock.advance(seconds=10)
    assert orchestrator.tick(tenant_id="tnt_a")["promoted_retries"] == 1
    second = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
    assert second is not None and second.attempt_count == 2
    final = orchestrator.fail_task(
        tenant_id="tnt_a",
        task_id=second.task_id,
        worker_id="worker",
        failure_class=FailureClass.CONNECTOR_RATE_LIMIT,
        message="still rate limited",
    )
    assert final.status == EngagementStatus.FAILED
    assert final.tasks[0].status == TaskStatus.FAILED


def test_pre_execution_gate_blocks_claim_until_approved(orchestrator, database):
    seed_engagement(database)
    workflow = WorkflowDefinition(
        workflow_version="1",
        tasks=[
            TaskDefinition(
                key="issue-report",
                task_type="report",
                human_gate="report_issuance",
                human_gate_position="before",
            )
        ],
    )
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=workflow
    )
    snapshot = orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    task = snapshot.tasks[0]
    assert snapshot.status == EngagementStatus.WAITING_APPROVAL
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker") is None

    approved = orchestrator.approve_gate(
        tenant_id="tnt_a",
        task_id=task.task_id,
        decision=GateDecision(actor_id="reviewer", reason="Report is approved for issue."),
    )
    assert approved.tasks[0].status == TaskStatus.READY
    assert orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker") is not None


def test_post_execution_gate_requires_approval_before_success(orchestrator, database):
    seed_engagement(database)
    workflow = WorkflowDefinition(
        workflow_version="1",
        tasks=[
            TaskDefinition(
                key="finding",
                task_type="finding",
                human_gate="finding_approval",
                human_gate_position="after",
            )
        ],
    )
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=workflow
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
    assert lease is not None
    pending = orchestrator.complete_task(
        tenant_id="tnt_a",
        task_id=lease.task_id,
        worker_id="worker",
        result=TaskExecutionResult(result={"finding": "proposed"}),
    )
    assert pending.status == EngagementStatus.WAITING_APPROVAL
    assert pending.tasks[0].status == TaskStatus.WAITING_APPROVAL

    approved = orchestrator.approve_gate(
        tenant_id="tnt_a",
        task_id=lease.task_id,
        decision=GateDecision(actor_id="reviewer", reason="Evidence threshold is met."),
    )
    assert approved.status == EngagementStatus.COMPLETED
    assert approved.tasks[0].status == TaskStatus.SUCCEEDED


def test_expired_lease_is_recovered_and_reclaimed(orchestrator, database, clock):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    first = orchestrator.claim_next(
        tenant_id="tnt_a", worker_id="worker-a", lease_seconds=5
    )
    assert first is not None
    clock.advance(seconds=6)
    maintenance = orchestrator.tick(tenant_id="tnt_a")
    assert maintenance["expired_leases"] == 1
    clock.advance(seconds=5)
    orchestrator.tick(tenant_id="tnt_a")
    second = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker-b")
    assert second is not None
    assert second.task_id == first.task_id
    assert second.attempt_count == 2


def test_cancellation_clears_active_leases(orchestrator, database):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
    assert lease is not None

    snapshot = orchestrator.cancel_engagement(
        tenant_id="tnt_a",
        engagement_id="eng_1",
        actor_id="sponsor",
        reason="Scope is no longer authorized.",
    )
    assert snapshot.status == EngagementStatus.CANCELLED
    assert all(task.status == TaskStatus.CANCELLED for task in snapshot.tasks)
    assert all(task.lease_owner is None for task in snapshot.tasks)


def test_domain_state_events_and_outbox_commit_together(orchestrator, database):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")

    with database.read_session() as session:
        events = AuditEventRepository(session).list("tnt_a", "eng_1")
        outbox = OutboxRepository(session).pending(limit=100)
    assert any(event["event_type"] == "orchestration.workflow.compiled" for event in events)
    assert len(outbox) == len(events)


def test_event_replay_matches_canonical_state(orchestrator, database):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=linear_workflow()
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
    assert lease is not None
    orchestrator.complete_task(
        tenant_id="tnt_a",
        task_id=lease.task_id,
        worker_id="worker",
        result=TaskExecutionResult(result={"ok": True}),
    )

    comparison = verify_replay(
        database,
        orchestrator,
        tenant_id="tnt_a",
        engagement_id="eng_1",
    )
    assert comparison.matches


def test_local_worker_classifies_retryable_handler_failure(orchestrator, database, clock):
    seed_engagement(database)
    orchestrator.compile_workflow(
        tenant_id="tnt_a",
        engagement_id="eng_1",
        workflow=WorkflowDefinition(
            workflow_version="1",
            tasks=[
                TaskDefinition(
                    key="sync",
                    task_type="connector",
                    retry_policy=RetryPolicy(initial_delay_seconds=1),
                )
            ],
        ),
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    calls = 0

    def handler(_lease):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableTaskError(
                "temporary outage", failure_class=FailureClass.TRANSIENT_INFRASTRUCTURE
            )
        return TaskExecutionResult(result={"synced": True})

    worker = LocalWorker(
        orchestrator=orchestrator,
        tenant_id="tnt_a",
        worker_id="local-worker",
        handlers={"connector": handler},
    )
    first = worker.run_once(engagement_id="eng_1")
    assert first is not None and first.outcome == "retry_scheduled"
    clock.advance(seconds=1)
    second = worker.run_once(engagement_id="eng_1")
    assert second is not None and second.outcome == "completed"
    assert orchestrator.snapshot(tenant_id="tnt_a", engagement_id="eng_1").status == (
        EngagementStatus.COMPLETED
    )


def test_terminal_failure_blocks_dependents_after_parallel_work_finishes(
    orchestrator, database
):
    seed_engagement(database)
    workflow = WorkflowDefinition(
        workflow_version="1",
        tasks=[
            TaskDefinition(key="branch-a", task_type="branch"),
            TaskDefinition(key="branch-b", task_type="branch"),
            TaskDefinition(
                key="join",
                task_type="join",
                dependencies=[
                    DependencyDefinition(task_key="branch-a"),
                    DependencyDefinition(task_key="branch-b"),
                ],
            ),
        ],
    )
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=workflow
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    first = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker-a")
    second = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker-b")
    assert first is not None and second is not None

    after_failure = orchestrator.fail_task(
        tenant_id="tnt_a",
        task_id=first.task_id,
        worker_id="worker-a",
        failure_class=FailureClass.DETERMINISTIC_TEST_FAILURE,
        message="test runtime failed permanently",
        force_retryable=False,
    )
    assert after_failure.status == EngagementStatus.RUNNING

    final = orchestrator.complete_task(
        tenant_id="tnt_a",
        task_id=second.task_id,
        worker_id="worker-b",
        result=TaskExecutionResult(result={"ok": True}),
    )
    states = by_key(final)
    assert final.status == EngagementStatus.FAILED
    assert states["join"].status == TaskStatus.BLOCKED


def test_gate_rejection_fails_task_and_blocks_downstream(orchestrator, database):
    seed_engagement(database)
    workflow = WorkflowDefinition(
        workflow_version="1",
        tasks=[
            TaskDefinition(
                key="finding",
                task_type="finding",
                human_gate="finding_approval",
                human_gate_position="after",
            ),
            TaskDefinition(
                key="report",
                task_type="report",
                dependencies=[DependencyDefinition(task_key="finding")],
            ),
        ],
    )
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=workflow
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    lease = orchestrator.claim_next(tenant_id="tnt_a", worker_id="worker")
    assert lease is not None
    orchestrator.complete_task(
        tenant_id="tnt_a",
        task_id=lease.task_id,
        worker_id="worker",
        result=TaskExecutionResult(result={"finding": "proposed"}),
    )

    rejected = orchestrator.reject_gate(
        tenant_id="tnt_a",
        task_id=lease.task_id,
        decision=GateDecision(
            actor_id="reviewer", reason="The proposed finding is not sufficiently supported."
        ),
    )
    states = by_key(rejected)
    assert rejected.status == EngagementStatus.FAILED
    assert states["finding"].status == TaskStatus.FAILED
    assert states["report"].status == TaskStatus.BLOCKED


def test_deadline_failure_occurs_without_worker_claim(orchestrator, database, clock):
    seed_engagement(database)
    workflow = WorkflowDefinition(
        workflow_version="1",
        tasks=[TaskDefinition(key="time-boxed", task_type="test", deadline_seconds=5)],
    )
    orchestrator.compile_workflow(
        tenant_id="tnt_a", engagement_id="eng_1", workflow=workflow
    )
    orchestrator.start_engagement(tenant_id="tnt_a", engagement_id="eng_1")
    clock.advance(seconds=6)

    maintenance = orchestrator.tick(tenant_id="tnt_a")
    snapshot = orchestrator.snapshot(tenant_id="tnt_a", engagement_id="eng_1")
    assert maintenance["overdue_tasks"] == 1
    assert snapshot.status == EngagementStatus.FAILED
    assert snapshot.tasks[0].error_class == "deadline_exceeded"
