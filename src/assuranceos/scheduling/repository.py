from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    AuditPlan,
    AuditSchedule,
    EngagementTemplate,
    ScheduleCursor,
    ScheduleOccurrence,
)

from .definitions import OccurrenceStatus


class SchedulingRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_plan(self, tenant_id: str, plan_id: str) -> AuditPlan | None:
        return self.session.scalar(
            select(AuditPlan).where(
                AuditPlan.tenant_id == tenant_id,
                AuditPlan.plan_id == plan_id,
            )
        )

    def get_template(self, tenant_id: str, template_id: str) -> EngagementTemplate | None:
        return self.session.scalar(
            select(EngagementTemplate).where(
                EngagementTemplate.tenant_id == tenant_id,
                EngagementTemplate.template_id == template_id,
            )
        )

    def get_schedule(
        self, tenant_id: str, schedule_id: str, *, lock: bool = False
    ) -> AuditSchedule | None:
        stmt = select(AuditSchedule).where(
            AuditSchedule.tenant_id == tenant_id,
            AuditSchedule.schedule_id == schedule_id,
        )
        if lock and self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)

    def list_active_schedules(
        self, tenant_id: str, *, schedule_id: str | None = None
    ) -> list[AuditSchedule]:
        stmt = select(AuditSchedule).where(
            AuditSchedule.tenant_id == tenant_id,
            AuditSchedule.status == "active",
        )
        if schedule_id is not None:
            stmt = stmt.where(AuditSchedule.schedule_id == schedule_id)
        return list(self.session.scalars(stmt.order_by(AuditSchedule.schedule_id)))

    def get_or_create_cursor(self, schedule: AuditSchedule) -> ScheduleCursor:
        cursor = self.session.get(ScheduleCursor, schedule.schedule_id)
        if cursor is None:
            cursor = ScheduleCursor(schedule_id=schedule.schedule_id, tenant_id=schedule.tenant_id)
            self.session.add(cursor)
            self.session.flush()
        return cursor

    def acquire_cursor_lease(
        self,
        *,
        schedule: AuditSchedule,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> ScheduleCursor | None:
        self.get_or_create_cursor(schedule)
        result = self.session.execute(
            update(ScheduleCursor)
            .where(
                ScheduleCursor.schedule_id == schedule.schedule_id,
                or_(
                    ScheduleCursor.lease_expires_at.is_(None),
                    ScheduleCursor.lease_expires_at <= now,
                    ScheduleCursor.lease_owner == owner,
                ),
            )
            .values(
                lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None
        self.session.flush()
        return self.session.get(ScheduleCursor, schedule.schedule_id)

    def release_cursor(
        self,
        *,
        cursor: ScheduleCursor,
        evaluated_at: datetime,
        next_due_at: datetime | None,
    ) -> None:
        cursor.last_evaluated_at = evaluated_at
        cursor.next_due_at = next_due_at
        cursor.lease_owner = None
        cursor.lease_expires_at = None
        cursor.updated_at = evaluated_at

    def add_occurrence(self, occurrence: ScheduleOccurrence) -> tuple[ScheduleOccurrence, bool]:
        try:
            with self.session.begin_nested():
                self.session.add(occurrence)
                self.session.flush()
            return occurrence, True
        except IntegrityError:
            existing = self.get_occurrence_by_due(
                occurrence.tenant_id, occurrence.schedule_id, occurrence.nominal_due
            )
            if existing is None:
                raise
            return existing, False

    def get_occurrence(
        self, tenant_id: str, occurrence_id: str, *, lock: bool = False
    ) -> ScheduleOccurrence | None:
        stmt = select(ScheduleOccurrence).where(
            ScheduleOccurrence.tenant_id == tenant_id,
            ScheduleOccurrence.occurrence_id == occurrence_id,
        )
        if lock and self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)

    def get_occurrence_by_due(
        self, tenant_id: str, schedule_id: str, nominal_due: datetime
    ) -> ScheduleOccurrence | None:
        return self.session.scalar(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.tenant_id == tenant_id,
                ScheduleOccurrence.schedule_id == schedule_id,
                ScheduleOccurrence.nominal_due == nominal_due,
            )
        )

    def list_occurrences(
        self, tenant_id: str, schedule_id: str
    ) -> list[ScheduleOccurrence]:
        return list(
            self.session.scalars(
                select(ScheduleOccurrence)
                .where(
                    ScheduleOccurrence.tenant_id == tenant_id,
                    ScheduleOccurrence.schedule_id == schedule_id,
                )
                .order_by(ScheduleOccurrence.nominal_due)
            )
        )

    def list_retryable_occurrences(
        self, tenant_id: str, *, now: datetime, schedule_id: str | None = None
    ) -> list[ScheduleOccurrence]:
        statuses = [
            OccurrenceStatus.DEFERRED,
            OccurrenceStatus.PREFLIGHT_BLOCKED,
            OccurrenceStatus.LAUNCH_FAILED,
            OccurrenceStatus.LAUNCHING,
        ]
        stmt = select(ScheduleOccurrence).where(
            ScheduleOccurrence.tenant_id == tenant_id,
            ScheduleOccurrence.status.in_(statuses),
            ScheduleOccurrence.eligible_at <= now,
        )
        if schedule_id is not None:
            stmt = stmt.where(ScheduleOccurrence.schedule_id == schedule_id)
        return list(self.session.scalars(stmt.order_by(ScheduleOccurrence.eligible_at)))
