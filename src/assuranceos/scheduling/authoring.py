from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from assuranceos.db.models import AuditSchedule, ScheduleCursor
from assuranceos.db.repositories import (
    AuditEventRepository,
    OutboxRepository,
    PlanningRepository,
    new_id,
)
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from .definitions import (
    AuditPeriodRule,
    BlackoutPolicy,
    BusinessCalendarConfig,
    LaunchMode,
    MissedOccurrencePolicy,
    OverlapPolicy,
)
from .exceptions import ScheduleConfigurationError, ScheduleNotFoundError
from .recurrence import RecurrenceEngine
from .repository import SchedulingRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    plan_id: str = Field(min_length=1, max_length=64)
    template_id: str = Field(min_length=1, max_length=64)
    recurrence_rule: str = Field(min_length=1, max_length=2000)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    effective_from: datetime
    effective_until: datetime | None = None
    audit_period_rule: AuditPeriodRule = Field(default_factory=AuditPeriodRule)
    business_calendar: BusinessCalendarConfig = Field(default_factory=BusinessCalendarConfig)
    blackout_policy: BlackoutPolicy = Field(default_factory=BlackoutPolicy)
    preflight_policy: dict[str, Any] = Field(default_factory=dict)
    launch_mode: LaunchMode = LaunchMode.APPROVAL_REQUIRED
    missed_occurrence_policy: MissedOccurrencePolicy = MissedOccurrencePolicy.LAUNCH_LATEST
    catch_up_limit: int = Field(default=12, ge=1, le=1000)
    overlap_policy: OverlapPolicy = OverlapPolicy.PREVENT
    max_concurrent_engagements: int = Field(default=1, ge=1, le=1000)


class ScheduleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4000)


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schedule_id: str
    tenant_id: str
    plan_id: str
    template_id: str
    name: str
    version: int
    status: str
    recurrence_rule: str
    timezone: str
    effective_from: datetime
    effective_until: datetime | None
    audit_period_rule_json: dict[str, Any]
    business_calendar_json: dict[str, Any]
    blackout_policy_json: dict[str, Any]
    preflight_policy_json: dict[str, Any]
    launch_mode: str
    missed_occurrence_policy: str
    catch_up_limit: int
    overlap_policy: str
    max_concurrent_engagements: int
    approved_at: datetime | None
    approved_by: str | None
    approval_reason: str | None
    disabled_at: datetime | None
    disabled_by: str | None
    disable_reason: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleAuthoringService:
    """Versioned authoring and approval lifecycle for recurring audit schedules."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        recurrence: RecurrenceEngine | None = None,
    ):
        self.database = database
        self.clock = clock
        self.recurrence = recurrence or RecurrenceEngine()

    def create_draft(self, *, tenant_id: str, draft: ScheduleDraftInput) -> ScheduleView:
        self._validate_definition(draft)
        now = self.clock()
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            plan = repository.get_plan(tenant_id, draft.plan_id)
            template = repository.get_template(tenant_id, draft.template_id)
            if plan is None:
                raise ScheduleConfigurationError(f"audit plan {draft.plan_id!r} was not found")
            if template is None:
                raise ScheduleConfigurationError(
                    f"engagement template {draft.template_id!r} was not found"
                )
            version = int(
                session.scalar(
                    select(func.max(AuditSchedule.version)).where(
                        AuditSchedule.tenant_id == tenant_id,
                        AuditSchedule.name == draft.name,
                    )
                )
                or 0
            ) + 1
            row = AuditSchedule(
                schedule_id=new_id("sch"),
                tenant_id=tenant_id,
                plan_id=draft.plan_id,
                template_id=draft.template_id,
                name=draft.name,
                version=version,
                status="draft",
                recurrence_rule=draft.recurrence_rule,
                timezone=draft.timezone,
                effective_from=self._aware(draft.effective_from),
                effective_until=(
                    self._aware(draft.effective_until) if draft.effective_until else None
                ),
                audit_period_rule_json=draft.audit_period_rule.model_dump(mode="json"),
                business_calendar_json=draft.business_calendar.model_dump(mode="json"),
                blackout_policy_json=draft.blackout_policy.model_dump(mode="json"),
                preflight_policy_json=dict(draft.preflight_policy),
                launch_mode=draft.launch_mode,
                missed_occurrence_policy=draft.missed_occurrence_policy,
                catch_up_limit=draft.catch_up_limit,
                overlap_policy=draft.overlap_policy,
                max_concurrent_engagements=draft.max_concurrent_engagements,
                created_at=now,
                updated_at=now,
            )
            PlanningRepository(session).add_schedule(row)
            self._record(session, row, "scheduler.schedule.draft_created", now, {})
            return ScheduleView.model_validate(row)

    def revise(
        self, *, tenant_id: str, schedule_id: str, draft: ScheduleDraftInput
    ) -> ScheduleView:
        with self.database.read_session() as session:
            current = SchedulingRepository(session).get_schedule(tenant_id, schedule_id)
            if current is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            if current.status == "active":
                # Active records remain immutable; a revision is a new version with the same name.
                draft = draft.model_copy(update={"name": current.name})
        return self.create_draft(tenant_id=tenant_id, draft=draft)

    def approve(
        self, *, tenant_id: str, schedule_id: str, decision: ScheduleDecision
    ) -> ScheduleView:
        now = self.clock()
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            row = repository.get_schedule(tenant_id, schedule_id, lock=True)
            if row is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            if row.status == "active":
                return ScheduleView.model_validate(row)
            if row.status != "draft":
                raise ScheduleConfigurationError(
                    f"schedule cannot be approved from state {row.status!r}"
                )
            plan = repository.get_plan(tenant_id, row.plan_id)
            template = repository.get_template(tenant_id, row.template_id)
            if plan is None or plan.status != "approved":
                raise ScheduleConfigurationError("schedule requires an approved audit plan")
            if template is None or template.status not in {"approved", "released"}:
                raise ScheduleConfigurationError("schedule requires a released engagement template")
            # Parse all executable configuration before changing canonical status.
            draft = self._draft_from_row(row)
            self._validate_definition(draft)
            active_rows = list(
                session.scalars(
                    select(AuditSchedule).where(
                        AuditSchedule.tenant_id == tenant_id,
                        AuditSchedule.name == row.name,
                        AuditSchedule.status == "active",
                        AuditSchedule.schedule_id != row.schedule_id,
                    )
                )
            )
            for active in active_rows:
                active.status = "superseded"
                active.disabled_at = now
                active.disabled_by = decision.actor_id
                active.disable_reason = f"superseded by schedule version {row.version}"
                self._record(
                    session,
                    active,
                    "scheduler.schedule.superseded",
                    now,
                    {"replacement_schedule_id": row.schedule_id},
                )
            row.status = "active"
            row.approved_at = now
            row.approved_by = decision.actor_id
            row.approval_reason = decision.reason
            row.disabled_at = None
            row.disabled_by = None
            row.disable_reason = None
            cursor = repository.get_or_create_cursor(row)
            cursor.next_due_at = self.recurrence.next_after(
                rule=row.recurrence_rule,
                timezone_name=row.timezone,
                effective_from=row.effective_from,
                after=max(self._aware(row.effective_from), self._aware(now)),
            )
            cursor.updated_at = now
            self._record(
                session,
                row,
                "scheduler.schedule.approved",
                now,
                {"actor_id": decision.actor_id, "reason": decision.reason},
            )
            return ScheduleView.model_validate(row)

    def disable(
        self, *, tenant_id: str, schedule_id: str, decision: ScheduleDecision
    ) -> ScheduleView:
        now = self.clock()
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            row = repository.get_schedule(tenant_id, schedule_id, lock=True)
            if row is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            if row.status == "disabled":
                return ScheduleView.model_validate(row)
            if row.status not in {"draft", "active"}:
                raise ScheduleConfigurationError(
                    f"schedule cannot be disabled from state {row.status!r}"
                )
            row.status = "disabled"
            row.disabled_at = now
            row.disabled_by = decision.actor_id
            row.disable_reason = decision.reason
            cursor = session.get(ScheduleCursor, row.schedule_id)
            if cursor:
                cursor.lease_owner = None
                cursor.lease_expires_at = None
                cursor.next_due_at = None
                cursor.updated_at = now
            self._record(
                session,
                row,
                "scheduler.schedule.disabled",
                now,
                {"actor_id": decision.actor_id, "reason": decision.reason},
            )
            return ScheduleView.model_validate(row)

    def get(self, *, tenant_id: str, schedule_id: str) -> ScheduleView:
        with self.database.read_session() as session:
            row = SchedulingRepository(session).get_schedule(tenant_id, schedule_id)
            if row is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            return ScheduleView.model_validate(row)

    def list(self, *, tenant_id: str, plan_id: str | None = None) -> list[ScheduleView]:
        with self.database.read_session() as session:
            rows = PlanningRepository(session).list_schedules(tenant_id, plan_id)
            return [ScheduleView.model_validate(row) for row in rows]

    def _validate_definition(self, draft: ScheduleDraftInput) -> None:
        if draft.effective_until and self._aware(draft.effective_until) < self._aware(
            draft.effective_from
        ):
            raise ScheduleConfigurationError("effective_until cannot precede effective_from")
        # Parsing and obtaining the first value validates both the rule and time zone.
        self.recurrence.next_after(
            rule=draft.recurrence_rule,
            timezone_name=draft.timezone,
            effective_from=draft.effective_from,
            after=self._aware(draft.effective_from) - self._one_second(),
        )

    @staticmethod
    def _one_second():
        from datetime import timedelta

        return timedelta(seconds=1)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @staticmethod
    def _draft_from_row(row: AuditSchedule) -> ScheduleDraftInput:
        return ScheduleDraftInput(
            name=row.name,
            plan_id=row.plan_id,
            template_id=row.template_id,
            recurrence_rule=row.recurrence_rule,
            timezone=row.timezone,
            effective_from=row.effective_from,
            effective_until=row.effective_until,
            audit_period_rule=AuditPeriodRule.model_validate(row.audit_period_rule_json),
            business_calendar=BusinessCalendarConfig.model_validate(
                row.business_calendar_json
            ),
            blackout_policy=BlackoutPolicy.model_validate(row.blackout_policy_json),
            preflight_policy=row.preflight_policy_json,
            launch_mode=LaunchMode(row.launch_mode),
            missed_occurrence_policy=MissedOccurrencePolicy(row.missed_occurrence_policy),
            catch_up_limit=row.catch_up_limit,
            overlap_policy=OverlapPolicy(row.overlap_policy),
            max_concurrent_engagements=row.max_concurrent_engagements,
        )

    @staticmethod
    def _record(
        session,
        schedule: AuditSchedule,
        event_type: str,
        now: datetime,
        payload: dict[str, Any],
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            tenant_id=schedule.tenant_id,
            occurred_at=now,
            payload={
                "schedule_id": schedule.schedule_id,
                "schedule_name": schedule.name,
                "schedule_version": schedule.version,
                "status": schedule.status,
                **payload,
            },
        )
        AuditEventRepository(session).append(event)
        OutboxRepository(session).add(
            tenant_id=schedule.tenant_id,
            aggregate_type="schedule",
            aggregate_id=schedule.schedule_id,
            event_type=event_type,
            payload=event.payload,
            idempotency_key=f"{event.event_id}:{event_type}",
        )
