from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from assuranceos.db.models import Engagement, EngagementTask, TaskAttempt, TaskDependency

from .definitions import EngagementStatus, TaskStatus


class OrchestrationRepository:
    """Persistence operations whose semantics are specific to workflow execution."""

    def __init__(self, session: Session):
        self.session = session

    def get_engagement(
        self, tenant_id: str, engagement_id: str, *, lock: bool = False
    ) -> Engagement | None:
        stmt = select(Engagement).where(
            Engagement.tenant_id == tenant_id,
            Engagement.engagement_id == engagement_id,
        )
        if lock and self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)

    def list_active_engagements(self, tenant_id: str) -> list[Engagement]:
        stmt = (
            select(Engagement)
            .where(
                Engagement.tenant_id == tenant_id,
                Engagement.status.in_(
                    [
                        EngagementStatus.RUNNING,
                        EngagementStatus.WAITING_APPROVAL,
                        EngagementStatus.BLOCKED,
                    ]
                ),
            )
            .order_by(Engagement.created_at, Engagement.engagement_id)
        )
        if self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list(self.session.scalars(stmt))

    def get_task(self, tenant_id: str, task_id: str) -> EngagementTask | None:
        return self.session.scalar(
            select(EngagementTask).where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.task_id == task_id,
            )
        )

    def list_tasks(self, tenant_id: str, engagement_id: str) -> list[EngagementTask]:
        stmt = (
            select(EngagementTask)
            .where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.engagement_id == engagement_id,
            )
            .order_by(EngagementTask.priority, EngagementTask.created_at, EngagementTask.task_key)
        )
        return list(self.session.scalars(stmt))

    def add_dependency(self, dependency: TaskDependency) -> TaskDependency:
        self.session.add(dependency)
        self.session.flush()
        return dependency

    def list_dependencies(
        self, tenant_id: str, engagement_id: str
    ) -> list[TaskDependency]:
        stmt = (
            select(TaskDependency)
            .join(EngagementTask, EngagementTask.task_id == TaskDependency.task_id)
            .where(
                TaskDependency.tenant_id == tenant_id,
                EngagementTask.engagement_id == engagement_id,
            )
            .order_by(TaskDependency.task_id, TaskDependency.depends_on_task_id)
        )
        return list(self.session.scalars(stmt))

    def claim_next(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        engagement_id: str | None = None,
        task_types: Iterable[str] | None = None,
        agent_roles: Iterable[str] | None = None,
    ) -> EngagementTask | None:
        filters = [
            EngagementTask.tenant_id == tenant_id,
            EngagementTask.status == TaskStatus.READY,
            or_(EngagementTask.available_at.is_(None), EngagementTask.available_at <= now),
            or_(EngagementTask.deadline_at.is_(None), EngagementTask.deadline_at > now),
        ]
        if engagement_id is not None:
            filters.append(EngagementTask.engagement_id == engagement_id)
        if task_types:
            filters.append(EngagementTask.task_type.in_(list(task_types)))
        if agent_roles:
            filters.append(EngagementTask.assigned_agent_role.in_(list(agent_roles)))

        stmt = (
            select(EngagementTask)
            .where(*filters)
            .order_by(EngagementTask.priority, EngagementTask.created_at, EngagementTask.task_key)
            .limit(20)
        )
        if self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        for candidate in self.session.scalars(stmt):
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            result = self.session.execute(
                update(EngagementTask)
                .where(
                    EngagementTask.task_id == candidate.task_id,
                    EngagementTask.tenant_id == tenant_id,
                    EngagementTask.status == TaskStatus.READY,
                    or_(
                        EngagementTask.available_at.is_(None),
                        EngagementTask.available_at <= now,
                    ),
                )
                .values(
                    status=TaskStatus.RUNNING,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    attempt_count=EngagementTask.attempt_count + 1,
                    started_at=now,
                    available_at=None,
                    error_class=None,
                    last_error=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self.session.flush()
                self.session.expire(candidate)
                self.session.refresh(candidate)
                return candidate
        return None


    def start_attempt(
        self,
        *,
        task: EngagementTask,
        worker_id: str,
        now: datetime,
        trace_id: str | None = None,
    ) -> TaskAttempt:
        from assuranceos.db.repositories import new_id

        attempt = TaskAttempt(
            attempt_id=new_id("att"),
            tenant_id=task.tenant_id,
            engagement_id=task.engagement_id,
            task_id=task.task_id,
            attempt_no=task.attempt_count,
            worker_id=worker_id,
            status="running",
            started_at=now,
            lease_expires_at=task.lease_expires_at,
            output_refs_json=[],
            trace_id=trace_id,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def get_attempt(
        self, tenant_id: str, task_id: str, attempt_no: int
    ) -> TaskAttempt | None:
        return self.session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.tenant_id == tenant_id,
                TaskAttempt.task_id == task_id,
                TaskAttempt.attempt_no == attempt_no,
            )
        )

    def list_attempts(
        self, tenant_id: str, *, engagement_id: str | None = None, task_id: str | None = None
    ) -> list[TaskAttempt]:
        filters = [TaskAttempt.tenant_id == tenant_id]
        if engagement_id is not None:
            filters.append(TaskAttempt.engagement_id == engagement_id)
        if task_id is not None:
            filters.append(TaskAttempt.task_id == task_id)
        stmt = select(TaskAttempt).where(*filters).order_by(
            TaskAttempt.started_at, TaskAttempt.task_id, TaskAttempt.attempt_no
        )
        return list(self.session.scalars(stmt))

    def update_attempt_lease(
        self, *, tenant_id: str, task_id: str, attempt_no: int, lease_expires_at: datetime
    ) -> bool:
        result = self.session.execute(
            update(TaskAttempt)
            .where(
                TaskAttempt.tenant_id == tenant_id,
                TaskAttempt.task_id == task_id,
                TaskAttempt.attempt_no == attempt_no,
                TaskAttempt.status == "running",
            )
            .values(lease_expires_at=lease_expires_at)
        )
        return result.rowcount == 1

    def finish_attempt(
        self,
        *,
        task: EngagementTask,
        status: str,
        now: datetime,
        failure_class: str | None = None,
        error_message: str | None = None,
        result: dict | None = None,
        output_refs: list[str] | None = None,
    ) -> TaskAttempt | None:
        attempt = self.get_attempt(task.tenant_id, task.task_id, task.attempt_count)
        if attempt is None or attempt.status != "running":
            return attempt
        attempt.status = status
        attempt.completed_at = now
        attempt.lease_expires_at = task.lease_expires_at
        attempt.failure_class = failure_class
        attempt.error_message = error_message
        attempt.result_json = result
        attempt.output_refs_json = list(output_refs or [])
        self.session.flush()
        return attempt

    def extend_lease(
        self,
        *,
        tenant_id: str,
        task_id: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> EngagementTask | None:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        result = self.session.execute(
            update(EngagementTask)
            .where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.task_id == task_id,
                EngagementTask.status == TaskStatus.RUNNING,
                EngagementTask.lease_owner == worker_id,
                EngagementTask.lease_expires_at > now,
            )
            .values(lease_expires_at=lease_expires_at, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        return self.get_task(tenant_id, task_id)

    def expired_running_tasks(self, tenant_id: str, now: datetime) -> list[EngagementTask]:
        stmt = (
            select(EngagementTask)
            .where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.status == TaskStatus.RUNNING,
                EngagementTask.lease_expires_at.is_not(None),
                EngagementTask.lease_expires_at <= now,
            )
            .order_by(EngagementTask.lease_expires_at, EngagementTask.task_id)
        )
        if self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list(self.session.scalars(stmt))

    def due_retry_tasks(self, tenant_id: str, now: datetime) -> list[EngagementTask]:
        stmt = (
            select(EngagementTask)
            .where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.status == TaskStatus.RETRY_WAIT,
                EngagementTask.available_at.is_not(None),
                EngagementTask.available_at <= now,
            )
            .order_by(EngagementTask.available_at, EngagementTask.task_id)
        )
        return list(self.session.scalars(stmt))

    def overdue_nonterminal_tasks(self, tenant_id: str, now: datetime) -> list[EngagementTask]:
        terminal = [
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        ]
        stmt = (
            select(EngagementTask)
            .where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.status.not_in(terminal),
                EngagementTask.deadline_at.is_not(None),
                EngagementTask.deadline_at <= now,
            )
            .order_by(EngagementTask.deadline_at, EngagementTask.task_id)
        )
        return list(self.session.scalars(stmt))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def lease_matches(
        self,
        task: EngagementTask,
        *,
        worker_id: str,
        now: datetime,
    ) -> bool:
        return bool(
            task.status == TaskStatus.RUNNING
            and task.lease_owner == worker_id
            and task.lease_expires_at is not None
            and self._as_utc(task.lease_expires_at) > self._as_utc(now)
        )
