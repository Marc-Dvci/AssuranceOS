from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from assuranceos.db import Database
from assuranceos.db.models import AuditSchedule, Engagement, EngagementTemplate, ScheduleOccurrence
from assuranceos.db.repositories import AuditEventRepository, EngagementRepository, OutboxRepository
from assuranceos.models import AuditEvent
from assuranceos.orchestration import EngagementStatus, WorkflowDefinition
from assuranceos.orchestration.service import Orchestrator

from .calendar import BusinessCalendar
from .definitions import (
    AuditPeriodRule,
    BlackoutPolicy,
    BusinessCalendarConfig,
    LaunchMode,
    MissedOccurrencePolicy,
    OccurrenceDecision,
    OccurrenceSnapshot,
    OccurrenceStatus,
    PreflightContext,
    ScheduleEvaluationSummary,
    ScheduleSimulationItem,
)
from .exceptions import (
    OccurrenceNotFoundError,
    OccurrenceStateError,
    ScheduleConfigurationError,
    ScheduleNotFoundError,
)
from .periods import AuditPeriodCalculator
from .preflight import PreflightEvaluator
from .recurrence import RecurrenceEngine
from .repository import SchedulingRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _MaterializationResult:
    created: int
    candidate_ids: list[str]
    occurrence_ids: list[str]
    skipped: int
    deferred: int


class AuditScheduler:
    """Deterministic scheduler and automatic engagement launcher.

    Configuration is immutable in versioned schedule/template rows. Mutable execution position is
    isolated in ``schedule_cursors``; each nominal occurrence is a durable, idempotent record.
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        orchestrator: Orchestrator | None = None,
        recurrence: RecurrenceEngine | None = None,
        periods: AuditPeriodCalculator | None = None,
        preflight: PreflightEvaluator | None = None,
    ):
        self.database = database
        self.clock = clock
        self.orchestrator = orchestrator or Orchestrator(database, clock=clock)
        self.recurrence = recurrence or RecurrenceEngine()
        self.periods = periods or AuditPeriodCalculator()
        self.preflight = preflight or PreflightEvaluator()

    def simulate(
        self,
        *,
        tenant_id: str,
        schedule_id: str,
        window_start: datetime,
        window_end: datetime,
        limit: int = 500,
    ) -> list[ScheduleSimulationItem]:
        with self.database.read_session() as session:
            repository = SchedulingRepository(session)
            schedule = repository.get_schedule(tenant_id, schedule_id)
            if schedule is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            return self._simulate_schedule(schedule, window_start, window_end, limit=limit)

    def evaluate_due(
        self,
        *,
        tenant_id: str,
        context: PreflightContext | None = None,
        schedule_id: str | None = None,
        worker_id: str = "local-scheduler",
        lease_seconds: int = 60,
    ) -> ScheduleEvaluationSummary:
        now = self._aware(self.clock())
        context = context or PreflightContext()
        summary = ScheduleEvaluationSummary(tenant_id=tenant_id, evaluated_at=now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self.database.read_session() as session:
            repository = SchedulingRepository(session)
            if schedule_id is not None:
                requested = repository.get_schedule(tenant_id, schedule_id)
                if requested is None:
                    raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
                if requested.status != "active":
                    raise ScheduleConfigurationError(
                        f"schedule {schedule_id!r} is not active"
                    )
            schedule_ids = [
                schedule.schedule_id
                for schedule in repository.list_active_schedules(
                    tenant_id, schedule_id=schedule_id
                )
            ]
        for current_schedule_id in schedule_ids:
            materialized = self._materialize_due_occurrences(
                tenant_id=tenant_id,
                schedule_id=current_schedule_id,
                now=now,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            summary.schedules_evaluated += 1
            summary.occurrences_created += materialized.created
            summary.skipped += materialized.skipped
            summary.deferred += materialized.deferred
            summary.occurrence_ids.extend(
                occurrence_id
                for occurrence_id in materialized.occurrence_ids
                if occurrence_id not in summary.occurrence_ids
            )
            for occurrence_id in materialized.candidate_ids:
                self._process_occurrence(tenant_id, occurrence_id, context, summary)
        with self.database.read_session() as session:
            retryable = SchedulingRepository(session).list_retryable_occurrences(
                tenant_id, now=now, schedule_id=schedule_id
            )
            retry_ids = [
                row.occurrence_id
                for row in retryable
                if row.occurrence_id not in summary.occurrence_ids
            ]
        for occurrence_id in retry_ids:
            self._process_occurrence(tenant_id, occurrence_id, context, summary)
        return summary

    def approve_occurrence(
        self, *, tenant_id: str, occurrence_id: str, decision: OccurrenceDecision
    ) -> OccurrenceSnapshot:
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            occurrence = repository.get_occurrence(tenant_id, occurrence_id, lock=True)
            if occurrence is None:
                raise OccurrenceNotFoundError(f"occurrence {occurrence_id!r} was not found")
            if occurrence.status == OccurrenceStatus.LAUNCHED:
                return OccurrenceSnapshot.model_validate(occurrence)
            if occurrence.status in {
                OccurrenceStatus.CANCELLED,
                OccurrenceStatus.SKIPPED,
            }:
                raise OccurrenceStateError(
                    f"occurrence {occurrence_id!r} cannot be approved from "
                    f"{occurrence.status!r}"
                )
            if occurrence.status != OccurrenceStatus.WAITING_APPROVAL:
                raise OccurrenceStateError(
                    f"occurrence {occurrence_id!r} is {occurrence.status!r}, not waiting_approval"
                )
            occurrence.decision_by = decision.actor_id
            occurrence.decision_reason = decision.reason
            self._record(
                session,
                occurrence,
                "scheduler.occurrence.approved",
                {"actor_id": decision.actor_id},
            )
        summary = ScheduleEvaluationSummary(
            tenant_id=tenant_id, evaluated_at=self._aware(self.clock())
        )
        self._run_preflight_and_launch(
            tenant_id, occurrence_id, decision.preflight_context, summary, approval_granted=True
        )
        return self.get_occurrence(tenant_id=tenant_id, occurrence_id=occurrence_id)

    def cancel_occurrence(
        self, *, tenant_id: str, occurrence_id: str, decision: OccurrenceDecision
    ) -> OccurrenceSnapshot:
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            occurrence = repository.get_occurrence(tenant_id, occurrence_id)
            if occurrence is None:
                raise OccurrenceNotFoundError(f"occurrence {occurrence_id!r} was not found")
            if occurrence.status in {
                OccurrenceStatus.LAUNCHED,
                OccurrenceStatus.CANCELLED,
                OccurrenceStatus.SKIPPED,
            }:
                raise OccurrenceStateError(
                    f"occurrence {occurrence_id!r} cannot be cancelled from "
                    f"{occurrence.status!r}"
                )
            occurrence.status = OccurrenceStatus.CANCELLED
            occurrence.decision_by = decision.actor_id
            occurrence.decision_reason = decision.reason
            self._record(
                session,
                occurrence,
                "scheduler.occurrence.cancelled",
                {"actor_id": decision.actor_id},
            )
            return OccurrenceSnapshot.model_validate(occurrence)

    def get_occurrence(self, *, tenant_id: str, occurrence_id: str) -> OccurrenceSnapshot:
        with self.database.read_session() as session:
            occurrence = SchedulingRepository(session).get_occurrence(
                tenant_id, occurrence_id
            )
            if occurrence is None:
                raise OccurrenceNotFoundError(f"occurrence {occurrence_id!r} was not found")
            return OccurrenceSnapshot.model_validate(occurrence)

    def list_occurrences(
        self, *, tenant_id: str, schedule_id: str
    ) -> list[OccurrenceSnapshot]:
        with self.database.read_session() as session:
            repository = SchedulingRepository(session)
            if repository.get_schedule(tenant_id, schedule_id) is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            rows = repository.list_occurrences(tenant_id, schedule_id)
            return [OccurrenceSnapshot.model_validate(row) for row in rows]

    def _materialize_due_occurrences(
        self,
        *,
        tenant_id: str,
        schedule_id: str,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
    ) -> _MaterializationResult:
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            schedule = repository.get_schedule(tenant_id, schedule_id, lock=True)
            if schedule is None:
                raise ScheduleNotFoundError(f"schedule {schedule_id!r} was not found")
            cursor = repository.acquire_cursor_lease(
                schedule=schedule, owner=worker_id, now=now, lease_seconds=lease_seconds
            )
            if cursor is None:
                return _MaterializationResult(0, [], [], 0, 0)
            start = (
                self._aware(cursor.last_evaluated_at)
                if cursor.last_evaluated_at is not None
                else self._aware(schedule.effective_from) - timedelta(microseconds=1)
            )
            end = (
                min(now, self._aware(schedule.effective_until))
                if schedule.effective_until
                else now
            )
            if end <= start:
                next_due = self.recurrence.next_after(
                    rule=schedule.recurrence_rule,
                    timezone_name=schedule.timezone,
                    effective_from=schedule.effective_from,
                    after=end,
                )
                repository.release_cursor(
                    cursor=cursor, evaluated_at=now, next_due_at=next_due
                )
                return _MaterializationResult(0, [], [], 0, 0)
            due_values = self.recurrence.occurrences_between(
                rule=schedule.recurrence_rule,
                timezone_name=schedule.timezone,
                effective_from=schedule.effective_from,
                start_exclusive=start,
                end_inclusive=end,
            )
            selected, skipped = self._select_missed_occurrences(schedule, due_values)
            created = 0
            candidates: list[str] = []
            occurrence_ids: list[str] = []
            skipped_count = 0
            deferred_count = 0
            for nominal_due in skipped + selected:
                occurrence, was_created = self._create_occurrence(
                    session,
                    repository,
                    schedule,
                    nominal_due,
                    forced_skip=nominal_due in skipped,
                )
                created += int(was_created)
                occurrence_ids.append(occurrence.occurrence_id)
                if occurrence.status == OccurrenceStatus.SKIPPED:
                    skipped_count += int(was_created)
                elif occurrence.status == OccurrenceStatus.DEFERRED:
                    deferred_count += int(was_created)
                else:
                    candidates.append(occurrence.occurrence_id)
            next_due = self.recurrence.next_after(
                rule=schedule.recurrence_rule,
                timezone_name=schedule.timezone,
                effective_from=schedule.effective_from,
                after=end,
            )
            repository.release_cursor(
                cursor=cursor, evaluated_at=now, next_due_at=next_due
            )
            return _MaterializationResult(
                created=created,
                candidate_ids=candidates,
                occurrence_ids=occurrence_ids,
                skipped=skipped_count,
                deferred=deferred_count,
            )

    def _create_occurrence(
        self,
        session: Session,
        repository: SchedulingRepository,
        schedule: AuditSchedule,
        nominal_due: datetime,
        *,
        forced_skip: bool,
    ) -> tuple[ScheduleOccurrence, bool]:
        existing = repository.get_occurrence_by_due(
            schedule.tenant_id, schedule.schedule_id, nominal_due
        )
        if existing is not None:
            return existing, False
        template = repository.get_template(schedule.tenant_id, schedule.template_id)
        if template is None:
            raise ScheduleConfigurationError("schedule references a missing engagement template")
        tz = self.recurrence.timezone(schedule.timezone)
        due_local = nominal_due.astimezone(tz)
        calendar_config = BusinessCalendarConfig.model_validate(
            schedule.business_calendar_json or {}
        )
        blackout_policy = BlackoutPolicy.model_validate(schedule.blackout_policy_json or {})
        blackout = BusinessCalendar(calendar_config).resolve_blackout(
            due_local, blackout_policy, tz
        )
        period_rule = AuditPeriodRule.model_validate(schedule.audit_period_rule_json or {})
        period_start, period_end = self.periods.calculate(due_local, period_rule)
        status = OccurrenceStatus.DUE
        reason = None
        if forced_skip:
            status = OccurrenceStatus.SKIPPED
            reason = "missed occurrence skipped by catch-up policy"
        elif blackout.action == "skip":
            status = OccurrenceStatus.SKIPPED
            reason = blackout.reason
        elif blackout.action == "delay":
            status = OccurrenceStatus.DEFERRED
            reason = blackout.reason
        occurrence = ScheduleOccurrence(
            occurrence_id=self._occurrence_id(schedule.schedule_id, nominal_due),
            tenant_id=schedule.tenant_id,
            schedule_id=schedule.schedule_id,
            nominal_due=nominal_due,
            eligible_at=blackout.eligible_at.astimezone(timezone.utc),
            period_start=period_start,
            period_end=period_end,
            status=status,
            decision_reason=reason,
            schedule_version=schedule.version,
            template_version=template.version,
            schedule_snapshot_json=self._schedule_snapshot(schedule),
            template_snapshot_json=self._template_snapshot(template),
        )
        session.add(occurrence)
        session.flush()
        self._record(session, occurrence, "scheduler.occurrence.created", {"status": status})
        return occurrence, True

    def _process_occurrence(
        self,
        tenant_id: str,
        occurrence_id: str,
        context: PreflightContext,
        summary: ScheduleEvaluationSummary,
    ) -> None:
        if occurrence_id not in summary.occurrence_ids:
            summary.occurrence_ids.append(occurrence_id)
        with self.database.read_session() as session:
            repository = SchedulingRepository(session)
            occurrence = repository.get_occurrence(tenant_id, occurrence_id)
            if occurrence is None:
                return
            schedule = repository.get_schedule(tenant_id, occurrence.schedule_id)
            if schedule is None:
                return
            mode = LaunchMode(schedule.launch_mode)
        if occurrence.status == OccurrenceStatus.LAUNCHING:
            self._attempt_launch(tenant_id, occurrence_id, summary)
            return
        if mode == LaunchMode.APPROVAL_REQUIRED:
            if occurrence.decision_by:
                self._run_preflight_and_launch(
                    tenant_id, occurrence_id, context, summary, approval_granted=True
                )
            else:
                self._mark_waiting_approval(tenant_id, occurrence_id, summary)
            return
        self._run_preflight_and_launch(
            tenant_id,
            occurrence_id,
            context,
            summary,
            approval_granted=bool(occurrence.decision_by),
        )

    def _run_preflight_and_launch(
        self,
        tenant_id: str,
        occurrence_id: str,
        context: PreflightContext,
        summary: ScheduleEvaluationSummary,
        *,
        approval_granted: bool = False,
    ) -> None:
        now = self._aware(self.clock())
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            occurrence = repository.get_occurrence(tenant_id, occurrence_id)
            if occurrence is None:
                raise OccurrenceNotFoundError(f"occurrence {occurrence_id!r} was not found")
            schedule = repository.get_schedule(tenant_id, occurrence.schedule_id)
            template = (
                repository.get_template(tenant_id, schedule.template_id)
                if schedule
                else None
            )
            if schedule is None or template is None:
                occurrence.status = OccurrenceStatus.PREFLIGHT_BLOCKED
                occurrence.decision_reason = "schedule or template is unavailable"
                self._record(
                    session,
                    occurrence,
                    "scheduler.preflight.blocked",
                    {"reason": occurrence.decision_reason},
                )
                summary.blocked += 1
                return
            report = self.preflight.evaluate(
                session,
                schedule=schedule,
                template=template,
                occurrence=occurrence,
                context=context,
                checked_at=now,
            )
            occurrence.preflight_result_json = report.model_dump(mode="json")
            occurrence.evaluated_at = now
            if not report.passed:
                occurrence.status = OccurrenceStatus.PREFLIGHT_BLOCKED
                occurrence.decision_reason = "; ".join(
                    check.code for check in report.checks if not check.passed
                )
                self._record(
                    session,
                    occurrence,
                    "scheduler.preflight.blocked",
                    occurrence.preflight_result_json,
                )
                summary.blocked += 1
                return
            self._record(
                session,
                occurrence,
                "scheduler.preflight.passed",
                occurrence.preflight_result_json,
            )
            if (
                schedule.launch_mode == LaunchMode.PREFLIGHT_THEN_APPROVAL
                and not approval_granted
            ):
                occurrence.status = OccurrenceStatus.WAITING_APPROVAL
                occurrence.decision_reason = "preflight passed; start approval required"
                summary.waiting_approval += 1
                return
        self._attempt_launch(tenant_id, occurrence_id, summary)

    def _attempt_launch(
        self,
        tenant_id: str,
        occurrence_id: str,
        summary: ScheduleEvaluationSummary,
    ) -> None:
        try:
            self._launch(tenant_id, occurrence_id)
            summary.launched += 1
        except Exception as exc:
            now = self._aware(self.clock())
            with self.database.transaction() as session:
                repository = SchedulingRepository(session)
                occurrence = repository.get_occurrence(
                    tenant_id, occurrence_id, lock=True
                )
                if occurrence is not None:
                    schedule = repository.get_schedule(
                        tenant_id, occurrence.schedule_id
                    )
                    retry_seconds = int(
                        (schedule.preflight_policy_json if schedule else {}).get(
                            "launch_retry_seconds", 300
                        )
                    )
                    occurrence.status = OccurrenceStatus.LAUNCH_FAILED
                    occurrence.last_error = str(exc)
                    occurrence.decision_reason = str(exc)
                    occurrence.eligible_at = now + timedelta(
                        seconds=max(retry_seconds, 1)
                    )
                    self._record(
                        session,
                        occurrence,
                        "scheduler.launch.failed",
                        {
                            "error": str(exc),
                            "retry_at": occurrence.eligible_at.isoformat(),
                            "launch_attempts": occurrence.launch_attempts,
                        },
                    )
            summary.failures += 1

    def _mark_waiting_approval(
        self, tenant_id: str, occurrence_id: str, summary: ScheduleEvaluationSummary
    ) -> None:
        with self.database.transaction() as session:
            occurrence = SchedulingRepository(session).get_occurrence(tenant_id, occurrence_id)
            if occurrence is None or occurrence.status == OccurrenceStatus.WAITING_APPROVAL:
                return
            occurrence.status = OccurrenceStatus.WAITING_APPROVAL
            occurrence.decision_reason = "start approval required before preflight"
            self._record(session, occurrence, "scheduler.occurrence.waiting_approval", {})
        summary.waiting_approval += 1

    def _launch(self, tenant_id: str, occurrence_id: str) -> None:
        now = self._aware(self.clock())
        with self.database.transaction() as session:
            repository = SchedulingRepository(session)
            occurrence = repository.get_occurrence(tenant_id, occurrence_id, lock=True)
            if occurrence is None:
                raise OccurrenceNotFoundError(
                    f"occurrence {occurrence_id!r} was not found"
                )
            if occurrence.status == OccurrenceStatus.LAUNCHED:
                return
            if occurrence.status in {
                OccurrenceStatus.CANCELLED,
                OccurrenceStatus.SKIPPED,
            }:
                raise OccurrenceStateError(
                    f"occurrence {occurrence_id!r} cannot launch from "
                    f"{occurrence.status!r}"
                )

            schedule = repository.get_schedule(
                tenant_id, occurrence.schedule_id, lock=True
            )
            template = (
                repository.get_template(tenant_id, schedule.template_id)
                if schedule
                else None
            )
            if schedule is None or template is None:
                raise ScheduleConfigurationError("schedule or template is unavailable")

            workflow = WorkflowDefinition.model_validate(
                template.workflow_definition_json
            )
            engagement_id = occurrence.engagement_id or self._engagement_id(occurrence_id)
            engagement = EngagementRepository(session).get(tenant_id, engagement_id)
            if engagement is None:
                engagement = Engagement(
                    engagement_id=engagement_id,
                    tenant_id=tenant_id,
                    template_id=template.template_id,
                    code=self._engagement_code(schedule, occurrence),
                    title=(
                        f"{template.name} — "
                        f"{occurrence.period_start} to {occurrence.period_end}"
                    ),
                    status=EngagementStatus.PLANNED,
                    audit_pack_ref=template.audit_pack_ref,
                    period_start=occurrence.period_start,
                    period_end=occurrence.period_end,
                    scope_json={
                        **template.scope_json,
                        "schedule_occurrence_id": occurrence_id,
                    },
                )
                session.add(engagement)

            retry_seconds = int(
                (schedule.preflight_policy_json or {}).get(
                    "launch_retry_seconds", 300
                )
            )
            occurrence.engagement_id = engagement_id
            occurrence.status = OccurrenceStatus.LAUNCHING
            occurrence.launch_attempts += 1
            occurrence.last_error = None
            # If the process terminates after this transaction, the occurrence becomes
            # eligible for an idempotent recovery attempt after this bounded interval.
            occurrence.eligible_at = now + timedelta(
                seconds=max(retry_seconds, 1)
            )
            self._record(
                session,
                occurrence,
                "scheduler.launch.started",
                {
                    "engagement_id": engagement_id,
                    "launch_attempt": occurrence.launch_attempts,
                    "recovery_eligible_at": occurrence.eligible_at.isoformat(),
                },
            )

        with self.database.read_session() as session:
            engagement = EngagementRepository(session).get(tenant_id, engagement_id)
            tasks = EngagementRepository(session).list_tasks(tenant_id, engagement_id)
        if not tasks:
            self.orchestrator.compile_workflow(
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                workflow=workflow,
            )
        if engagement is not None and engagement.status == EngagementStatus.PLANNED:
            self.orchestrator.start_engagement(
                tenant_id=tenant_id, engagement_id=engagement_id
            )

        with self.database.transaction() as session:
            occurrence = SchedulingRepository(session).get_occurrence(
                tenant_id, occurrence_id, lock=True
            )
            if occurrence is None:
                raise OccurrenceNotFoundError(
                    f"occurrence {occurrence_id!r} was not found"
                )
            if occurrence.status == OccurrenceStatus.LAUNCHED:
                return
            occurrence.status = OccurrenceStatus.LAUNCHED
            occurrence.launched_at = now
            occurrence.eligible_at = now
            occurrence.decision_reason = None
            occurrence.last_error = None
            self._record(
                session,
                occurrence,
                "scheduler.launch.completed",
                {"engagement_id": engagement_id},
            )

    def _simulate_schedule(
        self,
        schedule: AuditSchedule,
        window_start: datetime,
        window_end: datetime,
        *,
        limit: int,
    ) -> list[ScheduleSimulationItem]:
        window_start = self._aware(window_start)
        window_end = self._aware(window_end)
        if window_end < window_start:
            raise ValueError("window_end must not precede window_start")
        effective_end = (
            min(window_end, self._aware(schedule.effective_until))
            if schedule.effective_until
            else window_end
        )
        if effective_end < self._aware(schedule.effective_from):
            return []
        due_values = self.recurrence.occurrences_between(
            rule=schedule.recurrence_rule,
            timezone_name=schedule.timezone,
            effective_from=schedule.effective_from,
            start_exclusive=window_start - timedelta(microseconds=1),
            end_inclusive=effective_end,
            limit=limit,
        )
        tz = self.recurrence.timezone(schedule.timezone)
        calendar = BusinessCalendar(
            BusinessCalendarConfig.model_validate(schedule.business_calendar_json or {})
        )
        blackout_policy = BlackoutPolicy.model_validate(schedule.blackout_policy_json or {})
        period_rule = AuditPeriodRule.model_validate(schedule.audit_period_rule_json or {})
        result = []
        for due in due_values:
            local = due.astimezone(tz)
            blackout = calendar.resolve_blackout(local, blackout_policy, tz)
            period_start, period_end = self.periods.calculate(local, period_rule)
            result.append(
                ScheduleSimulationItem(
                    nominal_due=due,
                    eligible_at=blackout.eligible_at.astimezone(timezone.utc),
                    period_start=period_start,
                    period_end=period_end,
                    blackout_action=blackout.action,
                    blackout_reason=blackout.reason,
                )
            )
        return result

    @staticmethod
    def _select_missed_occurrences(
        schedule: AuditSchedule, due_values: list[datetime]
    ) -> tuple[list[datetime], list[datetime]]:
        if not due_values:
            return [], []
        policy = MissedOccurrencePolicy(schedule.missed_occurrence_policy)
        if policy == MissedOccurrencePolicy.SKIP:
            return [], due_values
        if policy == MissedOccurrencePolicy.LAUNCH_LATEST:
            return [due_values[-1]], due_values[:-1]
        selected = due_values[-schedule.catch_up_limit :]
        skipped = (
            due_values[: -schedule.catch_up_limit]
            if len(due_values) > schedule.catch_up_limit
            else []
        )
        return selected, skipped

    def _record(
        self,
        session: Session,
        occurrence: ScheduleOccurrence,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            tenant_id=occurrence.tenant_id,
            engagement_id=occurrence.engagement_id,
            occurred_at=self._aware(self.clock()),
            payload={
                "occurrence_id": occurrence.occurrence_id,
                "schedule_id": occurrence.schedule_id,
                **payload,
            },
        )
        AuditEventRepository(session).append(event)
        OutboxRepository(session).add(
            tenant_id=occurrence.tenant_id,
            aggregate_type="schedule_occurrence",
            aggregate_id=occurrence.occurrence_id,
            event_type=event_type,
            payload=event.payload,
            idempotency_key=event.event_id,
        )

    @staticmethod
    def _schedule_snapshot(schedule: AuditSchedule) -> dict[str, Any]:
        return {
            "schedule_id": schedule.schedule_id,
            "version": schedule.version,
            "plan_id": schedule.plan_id,
            "template_id": schedule.template_id,
            "recurrence_rule": schedule.recurrence_rule,
            "timezone": schedule.timezone,
            "audit_period_rule": schedule.audit_period_rule_json,
            "launch_mode": schedule.launch_mode,
            "overlap_policy": schedule.overlap_policy,
        }

    @staticmethod
    def _template_snapshot(template: EngagementTemplate) -> dict[str, Any]:
        return {
            "template_id": template.template_id,
            "version": template.version,
            "audit_pack_ref": template.audit_pack_ref,
            "objectives": template.objectives_json,
            "scope": template.scope_json,
            "workflow_version": template.workflow_definition_json.get("workflow_version"),
        }

    @staticmethod
    def _occurrence_id(schedule_id: str, nominal_due: datetime) -> str:
        digest = hashlib.sha256(
            f"{schedule_id}|{nominal_due.isoformat()}".encode()
        ).hexdigest()[:20]
        return f"occ_{digest}"

    @staticmethod
    def _engagement_id(occurrence_id: str) -> str:
        return f"eng_{hashlib.sha256(occurrence_id.encode()).hexdigest()[:20]}"

    @staticmethod
    def _engagement_code(schedule: AuditSchedule, occurrence: ScheduleOccurrence) -> str:
        name = re.sub(r"[^A-Za-z0-9]+", "-", schedule.name).strip("-").upper()[:40] or "AUDIT"
        due = occurrence.nominal_due.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
        return f"{name}-{due}-{occurrence.occurrence_id[-6:].upper()}"

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
