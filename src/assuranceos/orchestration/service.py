from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from assuranceos.db import Database
from assuranceos.db.models import Engagement, EngagementTask, TaskDependency
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository
from assuranceos.models import AuditEvent

from .compiler import WorkflowCompiler
from .definitions import (
    EngagementSnapshot,
    EngagementStatus,
    FailureClass,
    GateDecision,
    RetryPolicy,
    TaskAttemptSnapshot,
    TaskExecutionResult,
    TaskLease,
    TaskSnapshot,
    TaskStatus,
    WorkflowDefinition,
)
from .exceptions import (
    EngagementNotFoundError,
    InvalidStateTransitionError,
    LeaseConflictError,
    TaskNotFoundError,
)
from .repository import OrchestrationRepository


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
    }
)
FAILURE_TASK_STATUSES = frozenset(
    {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Orchestrator:
    """Durable, queue-neutral engagement workflow orchestrator.

    Public methods own their transaction. Every state transition is recorded in the canonical
    audit ledger and transactional outbox in the same commit as the domain state.
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        compiler: WorkflowCompiler | None = None,
    ):
        self.database = database
        self.clock = clock
        self.compiler = compiler or WorkflowCompiler()

    def compile_workflow(
        self,
        *,
        tenant_id: str,
        engagement_id: str,
        workflow: WorkflowDefinition,
    ) -> EngagementSnapshot:
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            engagement = self._require_engagement(repository, tenant_id, engagement_id, lock=True)
            tasks = self.compiler.compile(
                session,
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                workflow=workflow,
                compiled_at=now,
            )
            for task in tasks:
                self._record_task_created(session, task, now=now)
            self._record(
                session,
                event_type="orchestration.workflow.compiled",
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                aggregate_type="engagement",
                aggregate_id=engagement_id,
                payload={
                    "workflow_version": workflow.workflow_version,
                    "task_count": len(tasks),
                    "metadata": workflow.metadata,
                },
                now=now,
            )
            return self._snapshot(repository, engagement)

    def start_engagement(self, *, tenant_id: str, engagement_id: str) -> EngagementSnapshot:
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            engagement = self._require_engagement(repository, tenant_id, engagement_id, lock=True)
            if engagement.status != EngagementStatus.PLANNED:
                raise InvalidStateTransitionError(
                    f"engagement {engagement_id!r} is {engagement.status!r}, not planned"
                )
            if not repository.list_tasks(tenant_id, engagement_id):
                raise InvalidStateTransitionError("cannot start an engagement without a task graph")
            engagement.started_at = now
            self._transition_engagement(
                session,
                engagement,
                EngagementStatus.RUNNING,
                reason="engagement_started",
                now=now,
            )
            self._reconcile_engagement(session, repository, engagement, now=now)
            return self._snapshot(repository, engagement)

    def tick(self, *, tenant_id: str) -> dict[str, int]:
        """Perform deterministic maintenance without executing task handlers."""
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            expired = self._recover_expired_leases(session, repository, tenant_id, now=now)
            overdue = self._fail_overdue_tasks(session, repository, tenant_id, now=now)
            promoted = self._promote_due_retries(session, repository, tenant_id, now=now)
            reconciled = 0
            for engagement in repository.list_active_engagements(tenant_id):
                self._reconcile_engagement(session, repository, engagement, now=now)
                reconciled += 1
            return {
                "expired_leases": expired,
                "overdue_tasks": overdue,
                "promoted_retries": promoted,
                "reconciled_engagements": reconciled,
            }

    def claim_next(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        lease_seconds: int = 60,
        engagement_id: str | None = None,
        task_types: Iterable[str] | None = None,
        agent_roles: Iterable[str] | None = None,
    ) -> TaskLease | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = repository.claim_next(
                tenant_id=tenant_id,
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
                engagement_id=engagement_id,
                task_types=task_types,
                agent_roles=agent_roles,
            )
            if task is None:
                return None
            repository.start_attempt(task=task, worker_id=worker_id, now=now)
            self._record_task_transition(
                session,
                task,
                from_status=TaskStatus.READY,
                to_status=TaskStatus.RUNNING,
                reason="worker_claimed",
                now=now,
                extra={"worker_id": worker_id, "lease_expires_at": task.lease_expires_at},
            )
            return self._lease_from_task(task)

    def heartbeat(
        self,
        *,
        tenant_id: str,
        task_id: str,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> TaskLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = repository.extend_lease(
                tenant_id=tenant_id,
                task_id=task_id,
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
            )
            if task is None:
                raise LeaseConflictError(
                    "task lease is missing, expired, or owned by another worker"
                )
            repository.update_attempt_lease(
                tenant_id=tenant_id,
                task_id=task.task_id,
                attempt_no=task.attempt_count,
                lease_expires_at=task.lease_expires_at,
            )
            self._record(
                session,
                event_type="orchestration.task.heartbeat",
                tenant_id=tenant_id,
                engagement_id=task.engagement_id,
                task_id=task.task_id,
                aggregate_type="task",
                aggregate_id=task.task_id,
                payload={
                    "worker_id": worker_id,
                    "lease_expires_at": task.lease_expires_at,
                    "attempt_count": task.attempt_count,
                },
                now=now,
            )
            return self._lease_from_task(task)

    def complete_task(
        self,
        *,
        tenant_id: str,
        task_id: str,
        worker_id: str,
        result: TaskExecutionResult,
    ) -> EngagementSnapshot:
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = self._require_task(repository, tenant_id, task_id)
            self._require_lease(repository, task, worker_id=worker_id, now=now)
            from_status = TaskStatus(task.status)
            task.output_refs_json = list(result.output_refs)
            task.result_json = dict(result.result)
            task.lease_owner = None
            task.lease_expires_at = None
            task.error_class = None
            task.last_error = None

            gate_position = task.execution_policy_json.get("human_gate_position", "before")
            if task.human_gate and gate_position == "after":
                task.status = TaskStatus.WAITING_APPROVAL
                task.review_status = "pending"
                reason = "post_execution_gate_required"
            else:
                task.status = TaskStatus.SUCCEEDED
                task.completed_at = now
                reason = "execution_succeeded"
            repository.finish_attempt(
                task=task,
                status="succeeded",
                now=now,
                result=dict(result.result),
                output_refs=list(result.output_refs),
            )
            self._record_task_transition(
                session,
                task,
                from_status=from_status,
                to_status=TaskStatus(task.status),
                reason=reason,
                now=now,
                extra={"output_refs": task.output_refs_json},
            )
            engagement = self._require_engagement(
                repository, tenant_id, task.engagement_id, lock=True
            )
            self._reconcile_engagement(session, repository, engagement, now=now)
            return self._snapshot(repository, engagement)

    def fail_task(
        self,
        *,
        tenant_id: str,
        task_id: str,
        worker_id: str,
        failure_class: FailureClass,
        message: str,
        force_retryable: bool | None = None,
    ) -> EngagementSnapshot:
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = self._require_task(repository, tenant_id, task_id)
            self._require_lease(repository, task, worker_id=worker_id, now=now)
            self._apply_failure(
                session,
                task,
                failure_class=failure_class,
                message=message,
                now=now,
                force_retryable=force_retryable,
            )
            repository.finish_attempt(
                task=task,
                status=(
                    "retry_scheduled"
                    if task.status == TaskStatus.RETRY_WAIT
                    else "failed"
                ),
                now=now,
                failure_class=str(failure_class),
                error_message=message,
            )
            engagement = self._require_engagement(
                repository, tenant_id, task.engagement_id, lock=True
            )
            self._reconcile_engagement(session, repository, engagement, now=now)
            return self._snapshot(repository, engagement)

    def approve_gate(
        self,
        *,
        tenant_id: str,
        task_id: str,
        decision: GateDecision,
    ) -> EngagementSnapshot:
        return self._decide_gate(
            tenant_id=tenant_id,
            task_id=task_id,
            decision=decision,
            approved=True,
        )

    def reject_gate(
        self,
        *,
        tenant_id: str,
        task_id: str,
        decision: GateDecision,
    ) -> EngagementSnapshot:
        return self._decide_gate(
            tenant_id=tenant_id,
            task_id=task_id,
            decision=decision,
            approved=False,
        )

    def cancel_engagement(
        self,
        *,
        tenant_id: str,
        engagement_id: str,
        actor_id: str,
        reason: str,
    ) -> EngagementSnapshot:
        if not reason.strip():
            raise ValueError("cancellation reason is required")
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            engagement = self._require_engagement(repository, tenant_id, engagement_id, lock=True)
            if engagement.status in {EngagementStatus.COMPLETED, EngagementStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"cannot cancel engagement in state {engagement.status!r}"
                )
            for task in repository.list_tasks(tenant_id, engagement_id):
                if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
                    continue
                from_status = TaskStatus(task.status)
                task.status = TaskStatus.CANCELLED
                task.lease_owner = None
                task.lease_expires_at = None
                task.available_at = None
                task.completed_at = now
                task.error_class = "engagement_cancelled"
                task.last_error = reason
                if from_status == TaskStatus.RUNNING:
                    repository.finish_attempt(
                        task=task,
                        status="cancelled",
                        now=now,
                        failure_class="engagement_cancelled",
                        error_message=reason,
                    )
                self._record_task_transition(
                    session,
                    task,
                    from_status=from_status,
                    to_status=TaskStatus.CANCELLED,
                    reason="engagement_cancelled",
                    now=now,
                    extra={"actor_id": actor_id, "decision_reason": reason},
                )
            engagement.completed_at = now
            self._transition_engagement(
                session,
                engagement,
                EngagementStatus.CANCELLED,
                reason="cancelled_by_user",
                now=now,
                extra={"actor_id": actor_id, "decision_reason": reason},
            )
            return self._snapshot(repository, engagement)

    def snapshot(self, *, tenant_id: str, engagement_id: str) -> EngagementSnapshot:
        with self.database.read_session() as session:
            repository = OrchestrationRepository(session)
            engagement = self._require_engagement(repository, tenant_id, engagement_id)
            return self._snapshot(repository, engagement)


    def list_attempts(
        self,
        *,
        tenant_id: str,
        engagement_id: str | None = None,
        task_id: str | None = None,
    ) -> list[TaskAttemptSnapshot]:
        if engagement_id is None and task_id is None:
            raise ValueError("engagement_id or task_id is required")
        with self.database.read_session() as session:
            rows = OrchestrationRepository(session).list_attempts(
                tenant_id, engagement_id=engagement_id, task_id=task_id
            )
            return [TaskAttemptSnapshot.model_validate(row) for row in rows]

    def force_retry_task(
        self, *, tenant_id: str, task_id: str, actor_id: str, reason: str
    ) -> EngagementSnapshot:
        if not reason.strip():
            raise ValueError("administrative retry reason is required")
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = self._require_task(repository, tenant_id, task_id)
            current = TaskStatus(task.status)
            if current not in {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.RETRY_WAIT}:
                raise InvalidStateTransitionError(
                    f"task cannot be administratively retried from {current.value!r}"
                )
            task.status = TaskStatus.READY
            task.available_at = now
            task.completed_at = None
            task.lease_owner = None
            task.lease_expires_at = None
            task.error_class = None
            task.last_error = None
            task.review_status = None
            self._record_task_transition(
                session,
                task,
                from_status=current,
                to_status=TaskStatus.READY,
                reason="administrative_retry",
                now=now,
                extra={"actor_id": actor_id, "decision_reason": reason},
            )
            engagement = self._require_engagement(
                repository, tenant_id, task.engagement_id, lock=True
            )
            engagement.completed_at = None
            self._reconcile_engagement(session, repository, engagement, now=now)
            return self._snapshot(repository, engagement)

    def force_skip_task(
        self, *, tenant_id: str, task_id: str, actor_id: str, reason: str
    ) -> EngagementSnapshot:
        if not reason.strip():
            raise ValueError("administrative skip reason is required")
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = self._require_task(repository, tenant_id, task_id)
            current = TaskStatus(task.status)
            if current in {TaskStatus.SUCCEEDED, TaskStatus.SKIPPED, TaskStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"task cannot be administratively skipped from {current.value!r}"
                )
            if current == TaskStatus.RUNNING:
                repository.finish_attempt(
                    task=task,
                    status="cancelled",
                    now=now,
                    failure_class="administrative_skip",
                    error_message=reason,
                )
            task.status = TaskStatus.SKIPPED
            task.completed_at = now
            task.available_at = None
            task.lease_owner = None
            task.lease_expires_at = None
            task.error_class = None
            task.last_error = None
            self._record_task_transition(
                session,
                task,
                from_status=current,
                to_status=TaskStatus.SKIPPED,
                reason="administrative_skip",
                now=now,
                extra={"actor_id": actor_id, "decision_reason": reason},
            )
            engagement = self._require_engagement(
                repository, tenant_id, task.engagement_id, lock=True
            )
            self._reconcile_engagement(session, repository, engagement, now=now)
            return self._snapshot(repository, engagement)

    def _decide_gate(
        self,
        *,
        tenant_id: str,
        task_id: str,
        decision: GateDecision,
        approved: bool,
    ) -> EngagementSnapshot:
        now = self.clock()
        with self.database.transaction() as session:
            repository = OrchestrationRepository(session)
            task = self._require_task(repository, tenant_id, task_id)
            if task.status != TaskStatus.WAITING_APPROVAL or not task.human_gate:
                raise InvalidStateTransitionError("task is not waiting at a human gate")
            from_status = TaskStatus.WAITING_APPROVAL
            gate_position = task.execution_policy_json.get("human_gate_position", "before")
            task.review_status = "approved" if approved else "rejected"
            task.error_class = None if approved else FailureClass.HUMAN_REJECTED
            task.last_error = None if approved else decision.reason
            if approved:
                task.status = (
                    TaskStatus.READY if gate_position == "before" else TaskStatus.SUCCEEDED
                )
                if task.status == TaskStatus.SUCCEEDED:
                    task.completed_at = now
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = now
            self._record_task_transition(
                session,
                task,
                from_status=from_status,
                to_status=TaskStatus(task.status),
                reason="human_gate_approved" if approved else "human_gate_rejected",
                now=now,
                extra={
                    "gate": task.human_gate,
                    "gate_position": gate_position,
                    "actor_id": decision.actor_id,
                    "decision_reason": decision.reason,
                },
            )
            engagement = self._require_engagement(
                repository, tenant_id, task.engagement_id, lock=True
            )
            self._reconcile_engagement(session, repository, engagement, now=now)
            return self._snapshot(repository, engagement)

    def _recover_expired_leases(
        self,
        session: Session,
        repository: OrchestrationRepository,
        tenant_id: str,
        *,
        now: datetime,
    ) -> int:
        tasks = repository.expired_running_tasks(tenant_id, now)
        for task in tasks:
            self._apply_failure(
                session,
                task,
                failure_class=FailureClass.LEASE_EXPIRED,
                message="worker lease expired before task completion",
                now=now,
            )
            repository.finish_attempt(
                task=task,
                status="lease_expired",
                now=now,
                failure_class=str(FailureClass.LEASE_EXPIRED),
                error_message="worker lease expired before task completion",
            )
        return len(tasks)

    def _promote_due_retries(
        self,
        session: Session,
        repository: OrchestrationRepository,
        tenant_id: str,
        *,
        now: datetime,
    ) -> int:
        tasks = repository.due_retry_tasks(tenant_id, now)
        for task in tasks:
            from_status = TaskStatus.RETRY_WAIT
            task.status = TaskStatus.READY
            task.available_at = None
            self._record_task_transition(
                session,
                task,
                from_status=from_status,
                to_status=TaskStatus.READY,
                reason="retry_delay_elapsed",
                now=now,
            )
        return len(tasks)

    def _fail_overdue_tasks(
        self,
        session: Session,
        repository: OrchestrationRepository,
        tenant_id: str,
        *,
        now: datetime,
    ) -> int:
        tasks = repository.overdue_nonterminal_tasks(tenant_id, now)
        for task in tasks:
            from_status = TaskStatus(task.status)
            task.status = TaskStatus.FAILED
            task.completed_at = now
            task.lease_owner = None
            task.lease_expires_at = None
            task.available_at = None
            task.error_class = "deadline_exceeded"
            task.last_error = "task deadline elapsed before successful completion"
            if from_status == TaskStatus.RUNNING:
                repository.finish_attempt(
                    task=task,
                    status="failed",
                    now=now,
                    failure_class="deadline_exceeded",
                    error_message=task.last_error,
                )
            self._record_task_transition(
                session,
                task,
                from_status=from_status,
                to_status=TaskStatus.FAILED,
                reason="deadline_exceeded",
                now=now,
            )
        return len(tasks)

    def _apply_failure(
        self,
        session: Session,
        task: EngagementTask,
        *,
        failure_class: FailureClass,
        message: str,
        now: datetime,
        force_retryable: bool | None = None,
    ) -> None:
        from_status = TaskStatus(task.status)
        retry_policy = RetryPolicy.model_validate(
            task.execution_policy_json.get("retry_policy", {})
        )
        retryable = (
            force_retryable
            if force_retryable is not None
            else failure_class in retry_policy.retryable_failures
        )
        within_attempt_budget = task.attempt_count < retry_policy.max_attempts
        before_deadline = (
            task.deadline_at is None
            or self._as_utc(task.deadline_at) > self._as_utc(now)
        )
        task.lease_owner = None
        task.lease_expires_at = None
        task.error_class = failure_class
        task.last_error = message
        if retryable and within_attempt_budget and before_deadline:
            delay = retry_policy.delay_for_attempt(task.attempt_count)
            task.status = TaskStatus.RETRY_WAIT
            task.available_at = now + timedelta(seconds=delay)
            reason = "retry_scheduled"
            extra: dict[str, Any] = {"retry_delay_seconds": delay}
        else:
            task.status = TaskStatus.FAILED
            task.available_at = None
            task.completed_at = now
            reason = "execution_failed"
            extra = {"retryable": retryable, "attempt_budget_remaining": within_attempt_budget}
        self._record_task_transition(
            session,
            task,
            from_status=from_status,
            to_status=TaskStatus(task.status),
            reason=reason,
            now=now,
            extra={"failure_class": failure_class, "message": message, **extra},
        )

    def _reconcile_engagement(
        self,
        session: Session,
        repository: OrchestrationRepository,
        engagement: Engagement,
        *,
        now: datetime,
    ) -> None:
        tasks = repository.list_tasks(engagement.tenant_id, engagement.engagement_id)
        if not tasks:
            return
        dependencies = repository.list_dependencies(engagement.tenant_id, engagement.engagement_id)
        dependency_map: dict[str, list[TaskDependency]] = defaultdict(list)
        for dependency in dependencies:
            dependency_map[dependency.task_id].append(dependency)
        tasks_by_id = {task.task_id: task for task in tasks}

        changed = True
        while changed:
            changed = False
            for task in tasks:
                if task.status != TaskStatus.PENDING:
                    continue
                task_dependencies = dependency_map.get(task.task_id, [])
                dependency_rows = [
                    tasks_by_id[item.depends_on_task_id] for item in task_dependencies
                ]
                if any(TaskStatus(row.status) in FAILURE_TASK_STATUSES for row in dependency_rows):
                    task.status = TaskStatus.BLOCKED
                    task.completed_at = now
                    task.error_class = "dependency_failed"
                    task.last_error = "one or more prerequisite tasks did not complete successfully"
                    self._record_task_transition(
                        session,
                        task,
                        from_status=TaskStatus.PENDING,
                        to_status=TaskStatus.BLOCKED,
                        reason="dependency_failed",
                        now=now,
                    )
                    changed = True
                    continue
                if not self._dependencies_satisfied(task_dependencies, tasks_by_id):
                    continue
                if (
                    task.deadline_at is not None
                    and self._as_utc(task.deadline_at) <= self._as_utc(now)
                ):
                    task.status = TaskStatus.FAILED
                    task.completed_at = now
                    task.error_class = "deadline_exceeded"
                    task.last_error = "task deadline elapsed before it became runnable"
                    to_status = TaskStatus.FAILED
                    reason = "deadline_exceeded"
                elif (
                    task.human_gate
                    and task.execution_policy_json.get("human_gate_position", "before") == "before"
                    and task.review_status != "approved"
                ):
                    task.status = TaskStatus.WAITING_APPROVAL
                    task.review_status = "pending"
                    to_status = TaskStatus.WAITING_APPROVAL
                    reason = "pre_execution_gate_required"
                else:
                    task.status = TaskStatus.READY
                    task.available_at = now
                    to_status = TaskStatus.READY
                    reason = "dependencies_satisfied"
                self._record_task_transition(
                    session,
                    task,
                    from_status=TaskStatus.PENDING,
                    to_status=to_status,
                    reason=reason,
                    now=now,
                )
                changed = True

        self._reconcile_engagement_status(session, engagement, tasks, now=now)

    @staticmethod
    def _dependencies_satisfied(
        dependencies: list[TaskDependency], tasks_by_id: dict[str, EngagementTask]
    ) -> bool:
        for dependency in dependencies:
            allowed = set(
                dependency.condition_json.get(
                    "allowed_statuses", ["succeeded", "skipped"]
                )
            )
            if tasks_by_id[dependency.depends_on_task_id].status not in allowed:
                return False
        return True

    def _reconcile_engagement_status(
        self,
        session: Session,
        engagement: Engagement,
        tasks: list[EngagementTask],
        *,
        now: datetime,
    ) -> None:
        if engagement.status == EngagementStatus.CANCELLED:
            return
        statuses = {TaskStatus(task.status) for task in tasks}
        if statuses.issubset({TaskStatus.SUCCEEDED, TaskStatus.SKIPPED}):
            engagement.completed_at = now
            target = EngagementStatus.COMPLETED
            reason = "all_tasks_completed"
        elif statuses.intersection({TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.RETRY_WAIT}):
            target = EngagementStatus.RUNNING
            reason = "work_in_progress"
        elif TaskStatus.WAITING_APPROVAL in statuses:
            target = EngagementStatus.WAITING_APPROVAL
            reason = "human_decision_required"
        elif TaskStatus.FAILED in statuses:
            engagement.completed_at = now
            target = EngagementStatus.FAILED
            reason = "task_failed"
        elif TaskStatus.BLOCKED in statuses:
            target = EngagementStatus.BLOCKED
            reason = "dependency_blocked"
        else:
            target = EngagementStatus.RUNNING
            reason = "waiting_for_dependencies"
        self._transition_engagement(session, engagement, target, reason=reason, now=now)

    def _transition_engagement(
        self,
        session: Session,
        engagement: Engagement,
        target: EngagementStatus,
        *,
        reason: str,
        now: datetime,
        extra: dict[str, Any] | None = None,
    ) -> None:
        current = EngagementStatus(engagement.status)
        if current == target:
            return
        engagement.status = target
        self._record(
            session,
            event_type="orchestration.engagement.transitioned",
            tenant_id=engagement.tenant_id,
            engagement_id=engagement.engagement_id,
            aggregate_type="engagement",
            aggregate_id=engagement.engagement_id,
            payload={
                "from_status": current,
                "to_status": target,
                "reason": reason,
                **(extra or {}),
            },
            now=now,
        )

    def _record_task_created(
        self, session: Session, task: EngagementTask, *, now: datetime
    ) -> None:
        self._record(
            session,
            event_type="orchestration.task.created",
            tenant_id=task.tenant_id,
            engagement_id=task.engagement_id,
            task_id=task.task_id,
            aggregate_type="task",
            aggregate_id=task.task_id,
            payload={
                "task_key": task.task_key,
                "task_type": task.task_type,
                "status": task.status,
                "definition_version": task.definition_version,
                "human_gate": task.human_gate,
            },
            now=now,
        )

    def _record_task_transition(
        self,
        session: Session,
        task: EngagementTask,
        *,
        from_status: TaskStatus,
        to_status: TaskStatus,
        reason: str,
        now: datetime,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._record(
            session,
            event_type="orchestration.task.transitioned",
            tenant_id=task.tenant_id,
            engagement_id=task.engagement_id,
            task_id=task.task_id,
            aggregate_type="task",
            aggregate_id=task.task_id,
            payload={
                "task_key": task.task_key,
                "from_status": from_status,
                "to_status": to_status,
                "reason": reason,
                "attempt_count": task.attempt_count,
                **(extra or {}),
            },
            now=now,
        )

    @staticmethod
    def _record(
        session: Session,
        *,
        event_type: str,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        now: datetime,
        engagement_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            tenant_id=tenant_id,
            engagement_id=engagement_id,
            task_id=task_id,
            occurred_at=now,
            payload=Orchestrator._json_safe(payload),
        )
        AuditEventRepository(session).append(event)
        OutboxRepository(session).add(
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=event.payload,
            idempotency_key=event.event_id,
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: Orchestrator._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [Orchestrator._json_safe(item) for item in value]
        if hasattr(value, "value"):
            return value.value
        return value

    @staticmethod
    def _require_engagement(
        repository: OrchestrationRepository,
        tenant_id: str,
        engagement_id: str,
        *,
        lock: bool = False,
    ) -> Engagement:
        engagement = repository.get_engagement(tenant_id, engagement_id, lock=lock)
        if engagement is None:
            raise EngagementNotFoundError(f"engagement {engagement_id!r} was not found")
        return engagement

    @staticmethod
    def _require_task(
        repository: OrchestrationRepository, tenant_id: str, task_id: str
    ) -> EngagementTask:
        task = repository.get_task(tenant_id, task_id)
        if task is None:
            raise TaskNotFoundError(f"task {task_id!r} was not found")
        return task

    @staticmethod
    def _require_lease(
        repository: OrchestrationRepository,
        task: EngagementTask,
        *,
        worker_id: str,
        now: datetime,
    ) -> None:
        if not repository.lease_matches(task, worker_id=worker_id, now=now):
            raise LeaseConflictError("task lease is missing, expired, or owned by another worker")

    @staticmethod
    def _lease_from_task(task: EngagementTask) -> TaskLease:
        assert task.lease_owner is not None
        assert task.lease_expires_at is not None
        return TaskLease(
            tenant_id=task.tenant_id,
            engagement_id=task.engagement_id,
            task_id=task.task_id,
            task_key=task.task_key,
            task_type=task.task_type,
            assigned_agent_role=task.assigned_agent_role,
            attempt_count=task.attempt_count,
            lease_owner=task.lease_owner,
            lease_expires_at=task.lease_expires_at,
            input_refs=list(task.input_refs_json),
            execution_policy=dict(task.execution_policy_json),
            model_policy=task.model_policy,
            tool_policy=task.tool_policy,
            deadline_at=task.deadline_at,
            human_gate=task.human_gate,
        )

    def _snapshot(
        self, repository: OrchestrationRepository, engagement: Engagement
    ) -> EngagementSnapshot:
        return EngagementSnapshot(
            tenant_id=engagement.tenant_id,
            engagement_id=engagement.engagement_id,
            status=EngagementStatus(engagement.status),
            tasks=[
                TaskSnapshot(
                    task_id=task.task_id,
                    task_key=task.task_key,
                    status=TaskStatus(task.status),
                    attempt_count=task.attempt_count,
                    lease_owner=task.lease_owner,
                    lease_expires_at=task.lease_expires_at,
                    available_at=task.available_at,
                    human_gate=task.human_gate,
                    review_status=task.review_status,
                    error_class=task.error_class,
                    output_refs=list(task.output_refs_json),
                )
                for task in repository.list_tasks(
                    engagement.tenant_id, engagement.engagement_id
                )
            ],
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
