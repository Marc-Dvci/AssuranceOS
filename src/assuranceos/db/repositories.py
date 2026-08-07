from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from assuranceos.models import AuditEvent

from .models import (
    AgentRelease,
    ApprovalDecision,
    AuditEventRow,
    AuditPlan,
    AuditSchedule,
    AuditUniverseEntity,
    Control,
    Engagement,
    EngagementTask,
    EngagementTemplate,
    EntityRelationship,
    EvidenceRecord,
    ExecutionTrace,
    Finding,
    IdempotencyRecord,
    ManagementResponse,
    OrganizationFact,
    OrganizationProfile,
    OutboxEvent,
    RemediationAction,
    Retest,
    Risk,
    RiskControlLink,
    ScheduleOccurrence,
    TaskDependency,
    Tenant,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


class DuplicateRecordError(ValueError):
    pass


class TenantRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, tenant: Tenant) -> Tenant:
        self.session.add(tenant)
        self._flush("tenant already exists")
        return tenant

    def get(self, tenant_id: str) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        return self.session.scalar(select(Tenant).where(Tenant.slug == slug))

    def _flush(self, message: str) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DuplicateRecordError(message) from exc


class OrganizationRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_profile(self, profile: OrganizationProfile) -> OrganizationProfile:
        self.session.add(profile)
        self.session.flush()
        return profile

    def add_fact(self, fact: OrganizationFact) -> OrganizationFact:
        self.session.add(fact)
        self.session.flush()
        return fact

    def get_profile(self, tenant_id: str, profile_id: str) -> OrganizationProfile | None:
        return self.session.scalar(
            select(OrganizationProfile).where(
                OrganizationProfile.tenant_id == tenant_id,
                OrganizationProfile.profile_id == profile_id,
            )
        )

    def latest_profile(self, tenant_id: str) -> OrganizationProfile | None:
        stmt = (
            select(OrganizationProfile)
            .where(OrganizationProfile.tenant_id == tenant_id)
            .order_by(OrganizationProfile.version.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def list_facts(self, tenant_id: str, profile_id: str) -> list[OrganizationFact]:
        stmt = (
            select(OrganizationFact)
            .where(
                OrganizationFact.tenant_id == tenant_id,
                OrganizationFact.profile_id == profile_id,
            )
            .order_by(OrganizationFact.fact_key)
        )
        return list(self.session.scalars(stmt))


class AuditUniverseRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_entity(self, entity: AuditUniverseEntity) -> AuditUniverseEntity:
        self.session.add(entity)
        self.session.flush()
        return entity

    def add_relationship(self, relationship: EntityRelationship) -> EntityRelationship:
        self.session.add(relationship)
        self.session.flush()
        return relationship

    def add_risk(self, risk: Risk) -> Risk:
        self.session.add(risk)
        self.session.flush()
        return risk

    def add_control(self, control: Control) -> Control:
        self.session.add(control)
        self.session.flush()
        return control

    def link_risk_control(self, link: RiskControlLink) -> RiskControlLink:
        self.session.add(link)
        self.session.flush()
        return link

    def list_entities(self, tenant_id: str) -> list[AuditUniverseEntity]:
        stmt = (
            select(AuditUniverseEntity)
            .where(AuditUniverseEntity.tenant_id == tenant_id)
            .order_by(AuditUniverseEntity.entity_type, AuditUniverseEntity.name)
        )
        return list(self.session.scalars(stmt))

    def list_risks(self, tenant_id: str) -> list[Risk]:
        stmt = select(Risk).where(Risk.tenant_id == tenant_id).order_by(Risk.code)
        return list(self.session.scalars(stmt))

    def list_controls(self, tenant_id: str) -> list[Control]:
        stmt = select(Control).where(Control.tenant_id == tenant_id).order_by(Control.code)
        return list(self.session.scalars(stmt))


class PlanningRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_plan(self, plan: AuditPlan) -> AuditPlan:
        self.session.add(plan)
        self.session.flush()
        return plan

    def add_template(self, template: EngagementTemplate) -> EngagementTemplate:
        self.session.add(template)
        self.session.flush()
        return template

    def add_schedule(self, schedule: AuditSchedule) -> AuditSchedule:
        self.session.add(schedule)
        self.session.flush()
        return schedule

    def add_occurrence(self, occurrence: ScheduleOccurrence) -> ScheduleOccurrence:
        self.session.add(occurrence)
        self.session.flush()
        return occurrence

    def get_schedule(self, tenant_id: str, schedule_id: str) -> AuditSchedule | None:
        return self.session.scalar(
            select(AuditSchedule).where(
                AuditSchedule.tenant_id == tenant_id,
                AuditSchedule.schedule_id == schedule_id,
            )
        )

    def list_schedules(self, tenant_id: str, plan_id: str | None = None) -> list[AuditSchedule]:
        stmt = select(AuditSchedule).where(AuditSchedule.tenant_id == tenant_id)
        if plan_id is not None:
            stmt = stmt.where(AuditSchedule.plan_id == plan_id)
        stmt = stmt.order_by(AuditSchedule.name, AuditSchedule.version)
        return list(self.session.scalars(stmt))

    def list_occurrences(self, tenant_id: str, schedule_id: str) -> list[ScheduleOccurrence]:
        stmt = (
            select(ScheduleOccurrence)
            .where(
                ScheduleOccurrence.tenant_id == tenant_id,
                ScheduleOccurrence.schedule_id == schedule_id,
            )
            .order_by(ScheduleOccurrence.nominal_due)
        )
        return list(self.session.scalars(stmt))


class EngagementRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, engagement: Engagement) -> Engagement:
        self.session.add(engagement)
        self.session.flush()
        return engagement

    def get(self, tenant_id: str, engagement_id: str) -> Engagement | None:
        return self.session.scalar(
            select(Engagement).where(
                Engagement.tenant_id == tenant_id,
                Engagement.engagement_id == engagement_id,
            )
        )

    def add_task(self, task: EngagementTask) -> EngagementTask:
        self.session.add(task)
        self.session.flush()
        return task

    def add_dependency(self, dependency: TaskDependency) -> TaskDependency:
        self.session.add(dependency)
        self.session.flush()
        return dependency

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
            .order_by(EngagementTask.created_at, EngagementTask.task_key)
        )
        return list(self.session.scalars(stmt))

    def transition_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_status: str,
        new_status: str,
        error_class: str | None = None,
    ) -> bool:
        stmt = (
            update(EngagementTask)
            .where(
                EngagementTask.tenant_id == tenant_id,
                EngagementTask.task_id == task_id,
                EngagementTask.status == expected_status,
            )
            .values(status=new_status, error_class=error_class)
        )
        result = self.session.execute(stmt)
        return result.rowcount == 1


class EvidenceRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, evidence: EvidenceRecord) -> EvidenceRecord:
        self.session.add(evidence)
        self.session.flush()
        return evidence

    def get(self, tenant_id: str, evidence_id: str) -> EvidenceRecord | None:
        return self.session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.evidence_id == evidence_id,
            )
        )

    def list_for_engagement(self, tenant_id: str, engagement_id: str) -> list[EvidenceRecord]:
        stmt = (
            select(EvidenceRecord)
            .where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.engagement_id == engagement_id,
            )
            .order_by(EvidenceRecord.collected_at, EvidenceRecord.evidence_id)
        )
        return list(self.session.scalars(stmt))


class FindingRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, finding: Finding) -> Finding:
        self.session.add(finding)
        self.session.flush()
        return finding

    def get(self, tenant_id: str, finding_id: str) -> Finding | None:
        return self.session.scalar(
            select(Finding).where(
                Finding.tenant_id == tenant_id,
                Finding.finding_id == finding_id,
            )
        )

    def add_decision(self, decision: ApprovalDecision) -> ApprovalDecision:
        self.session.add(decision)
        self.session.flush()
        return decision

    def list_decisions(self, tenant_id: str, finding_id: str) -> list[ApprovalDecision]:
        stmt = (
            select(ApprovalDecision)
            .where(
                ApprovalDecision.tenant_id == tenant_id,
                ApprovalDecision.finding_id == finding_id,
            )
            .order_by(ApprovalDecision.decided_at, ApprovalDecision.decision_id)
        )
        return list(self.session.scalars(stmt))


class RemediationRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_management_response(self, response: ManagementResponse) -> ManagementResponse:
        self.session.add(response)
        self.session.flush()
        return response

    def add_action(self, action: RemediationAction) -> RemediationAction:
        self.session.add(action)
        self.session.flush()
        return action

    def add_retest(self, retest: Retest) -> Retest:
        self.session.add(retest)
        self.session.flush()
        return retest

    def get_action(self, tenant_id: str, action_id: str) -> RemediationAction | None:
        return self.session.scalar(
            select(RemediationAction).where(
                RemediationAction.tenant_id == tenant_id,
                RemediationAction.action_id == action_id,
            )
        )

    def list_actions_for_finding(
        self, tenant_id: str, finding_id: str
    ) -> list[RemediationAction]:
        stmt = (
            select(RemediationAction)
            .where(
                RemediationAction.tenant_id == tenant_id,
                RemediationAction.finding_id == finding_id,
            )
            .order_by(RemediationAction.due_date, RemediationAction.action_id)
        )
        return list(self.session.scalars(stmt))


class AgentGovernanceRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_release(self, release: AgentRelease) -> AgentRelease:
        self.session.add(release)
        self.session.flush()
        return release

    def get_release(self, agent_id: str, version: str) -> AgentRelease | None:
        return self.session.scalar(
            select(AgentRelease).where(
                AgentRelease.agent_id == agent_id,
                AgentRelease.version == version,
            )
        )

    def add_trace(self, trace: ExecutionTrace) -> ExecutionTrace:
        self.session.add(trace)
        self.session.flush()
        return trace

    def list_traces(self, tenant_id: str, engagement_id: str) -> list[ExecutionTrace]:
        stmt = (
            select(ExecutionTrace)
            .where(
                ExecutionTrace.tenant_id == tenant_id,
                ExecutionTrace.engagement_id == engagement_id,
            )
            .order_by(ExecutionTrace.started_at, ExecutionTrace.trace_id)
        )
        return list(self.session.scalars(stmt))


class AuditEventRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _stream_id(event: AuditEvent) -> str:
        return event.task_id or event.engagement_id or f"tenant:{event.tenant_id}"

    def _next_sequence(self, tenant_id: str, stream_id: str) -> int:
        current = self.session.scalar(
            select(func.max(AuditEventRow.sequence_no)).where(
                AuditEventRow.tenant_id == tenant_id,
                AuditEventRow.stream_id == stream_id,
            )
        )
        return int(current or 0) + 1

    def append(self, event: AuditEvent) -> None:
        stream_id = self._stream_id(event)
        self.session.add(
            AuditEventRow(
                event_id=event.event_id,
                event_type=event.event_type,
                stream_id=stream_id,
                sequence_no=self._next_sequence(event.tenant_id, stream_id),
                tenant_id=event.tenant_id,
                engagement_id=event.engagement_id,
                task_id=event.task_id,
                occurred_at=event.occurred_at,
                payload_json=event.payload,
            )
        )
        self.session.flush()

    def append_many(self, events: Iterable[AuditEvent]) -> None:
        for event in events:
            self.append(event)

    def list(self, tenant_id: str, engagement_id: str | None = None) -> list[dict[str, Any]]:
        stmt = select(AuditEventRow).where(AuditEventRow.tenant_id == tenant_id)
        if engagement_id is not None:
            stmt = stmt.where(AuditEventRow.engagement_id == engagement_id)
        stmt = stmt.order_by(
            AuditEventRow.occurred_at,
            AuditEventRow.stream_id,
            AuditEventRow.sequence_no,
            AuditEventRow.event_id,
        )
        return [
            {
                "event_id": row.event_id,
                "event_type": row.event_type,
                "stream_id": row.stream_id,
                "sequence_no": row.sequence_no,
                "tenant_id": row.tenant_id,
                "engagement_id": row.engagement_id,
                "task_id": row.task_id,
                "occurred_at": row.occurred_at.isoformat(),
                "payload_json": json.dumps(row.payload_json, sort_keys=True),
                "payload": row.payload_json,
            }
            for row in self.session.scalars(stmt)
        ]

    def delete_for_tenant(self, tenant_id: str) -> int:
        result = self.session.execute(
            delete(AuditEventRow).where(AuditEventRow.tenant_id == tenant_id)
        )
        return result.rowcount


class OutboxRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboxEvent:
        event = OutboxEvent(
            outbox_id=new_id("obx"),
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload_json=payload,
            idempotency_key=idempotency_key,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def pending(self, *, now: datetime | None = None, limit: int = 100) -> list[OutboxEvent]:
        filters = [
            OutboxEvent.published_at.is_(None),
            OutboxEvent.dead_lettered_at.is_(None),
        ]
        if now is not None:
            filters.extend(
                [
                    OutboxEvent.available_at <= now,
                    or_(
                        OutboxEvent.lease_expires_at.is_(None),
                        OutboxEvent.lease_expires_at <= now,
                    ),
                ]
            )
        stmt = (
            select(OutboxEvent)
            .where(*filters)
            .order_by(OutboxEvent.available_at, OutboxEvent.occurred_at, OutboxEvent.outbox_id)
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def claim(
        self, *, worker_id: str, now: datetime, lease_seconds: int, limit: int = 100
    ) -> list[OutboxEvent]:
        if lease_seconds <= 0 or limit <= 0:
            raise ValueError("lease_seconds and limit must be positive")
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.dead_lettered_at.is_(None),
                OutboxEvent.available_at <= now,
                or_(
                    OutboxEvent.lease_expires_at.is_(None),
                    OutboxEvent.lease_expires_at <= now,
                ),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.occurred_at, OutboxEvent.outbox_id)
            .limit(limit)
        )
        if self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        claimed: list[OutboxEvent] = []
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        for candidate in self.session.scalars(stmt):
            result = self.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.outbox_id == candidate.outbox_id,
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.dead_lettered_at.is_(None),
                    OutboxEvent.available_at <= now,
                    or_(
                        OutboxEvent.lease_expires_at.is_(None),
                        OutboxEvent.lease_expires_at <= now,
                    ),
                )
                .values(lease_owner=worker_id, lease_expires_at=lease_expires_at)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self.session.expire(candidate)
                self.session.refresh(candidate)
                claimed.append(candidate)
        return claimed

    def mark_published(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        published_at: datetime,
        message_id: str,
    ) -> bool:
        result = self.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.outbox_id == outbox_id,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.lease_owner == worker_id,
            )
            .values(
                published_at=published_at,
                published_message_id=message_id,
                publish_attempts=OutboxEvent.publish_attempts + 1,
                lease_owner=None,
                lease_expires_at=None,
                last_error=None,
            )
        )
        return result.rowcount == 1

    def mark_failed(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        now: datetime,
        error: str,
        retry_delay_seconds: float,
        dead_letter: bool,
    ) -> bool:
        values: dict[str, Any] = {
            "publish_attempts": OutboxEvent.publish_attempts + 1,
            "last_error": error[:4000],
            "lease_owner": None,
            "lease_expires_at": None,
            "available_at": now + timedelta(seconds=max(retry_delay_seconds, 0)),
        }
        if dead_letter:
            values["dead_lettered_at"] = now
        result = self.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.outbox_id == outbox_id,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.lease_owner == worker_id,
            )
            .values(**values)
        )
        return result.rowcount == 1


class IdempotencyRepository:
    def __init__(self, session: Session):
        self.session = session

    def begin(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        operation: str,
        request_fingerprint: str,
    ) -> IdempotencyRecord:
        record = IdempotencyRecord(
            record_id=new_id("idem"),
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            operation=operation,
            request_fingerprint=request_fingerprint,
        )
        self.session.add(record)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DuplicateRecordError("idempotency key already exists") from exc
        return record

    def get(self, tenant_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        return self.session.scalar(stmt)
