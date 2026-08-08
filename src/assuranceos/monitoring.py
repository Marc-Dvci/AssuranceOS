"""Governed continuous assurance over released deterministic control tests."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from .control_testing import ControlTestRunRequest, ControlTestService
from .db.models import ContinuousMonitor, MonitorAlert, MonitorRun, Tenant
from .db.repositories import new_id
from .db.session import Database


class MonitorError(ValueError):
    pass


class MonitorDefinitionInput(BaseModel):
    monitor_key: str = Field(min_length=3, max_length=128)
    title: str = Field(min_length=3, max_length=255)
    test_id: str = Field(min_length=3, max_length=128)
    test_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    owner_ref: str = Field(min_length=1, max_length=255)
    reviewer_ref: str = Field(min_length=1, max_length=255)
    source_freshness_seconds: int = Field(ge=60)
    minimum_completeness: float = Field(ge=0, le=1)
    exception_threshold: int = Field(default=0, ge=0)
    deduplication_window_seconds: int = Field(ge=60)
    alert_budget: int = Field(ge=1)
    response_sla_seconds: int = Field(ge=60)
    independence_preserved: bool
    approval_ref: str = Field(min_length=1, max_length=255)
    approved_by: str = Field(min_length=1, max_length=255)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def independent_review(self):
        if self.owner_ref == self.reviewer_ref:
            raise ValueError("monitor owner and reviewer must be independent")
        if not self.independence_preserved:
            raise ValueError("monitor activation requires preserved audit independence")
        return self


class MonitorExecutionInput(BaseModel):
    source_observed_at: datetime
    source_completeness: float = Field(ge=0, le=1)
    test_request: ControlTestRunRequest


class ContinuousMonitoringService:
    def __init__(
        self,
        database: Database,
        tests: ControlTestService,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.tests = tests
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def activate(self, tenant_id: str, data: MonitorDefinitionInput) -> dict[str, Any]:
        self.tests.registry.get(data.test_id, data.test_version)
        now = self.clock()
        with self.database.transaction() as session:
            if session.get(Tenant, tenant_id) is None:
                raise MonitorError(f"tenant not found: {tenant_id}")
            existing = list(
                session.scalars(
                    select(ContinuousMonitor).where(
                        ContinuousMonitor.tenant_id == tenant_id,
                        ContinuousMonitor.monitor_key == data.monitor_key,
                    )
                )
            )
            for item in existing:
                if item.status == "active":
                    item.status = "retired"
            row = ContinuousMonitor(
                monitor_id=new_id("mon"),
                tenant_id=tenant_id,
                monitor_key=data.monitor_key,
                version=max((item.version for item in existing), default=0) + 1,
                title=data.title,
                status="active",
                test_id=data.test_id,
                test_version=data.test_version,
                owner_ref=data.owner_ref,
                reviewer_ref=data.reviewer_ref,
                source_freshness_seconds=data.source_freshness_seconds,
                minimum_completeness=data.minimum_completeness,
                exception_threshold=data.exception_threshold,
                deduplication_window_seconds=data.deduplication_window_seconds,
                alert_budget=data.alert_budget,
                response_sla_seconds=data.response_sla_seconds,
                independence_preserved=data.independence_preserved,
                approval_ref=data.approval_ref,
                approved_by=data.approved_by,
                approved_at=now,
                configuration_json=data.configuration,
            )
            session.add(row)
            session.flush()
            return self._monitor_view(row)

    def execute(
        self, tenant_id: str, monitor_id: str, data: MonitorExecutionInput
    ) -> dict[str, Any]:
        now = self.clock()
        if data.source_observed_at.tzinfo is None:
            raise MonitorError("source_observed_at must include a timezone")
        with self.database.read_session() as session:
            monitor = session.scalar(
                select(ContinuousMonitor).where(
                    ContinuousMonitor.tenant_id == tenant_id,
                    ContinuousMonitor.monitor_id == monitor_id,
                )
            )
            if monitor is None or monitor.status not in {"active", "suspended"}:
                raise MonitorError("active monitor not found")
            existing = session.scalar(
                select(MonitorRun).where(
                    MonitorRun.tenant_id == tenant_id,
                    MonitorRun.idempotency_key == data.test_request.idempotency_key,
                )
            )
            if existing is not None:
                return self._run_view(existing)
            test_id, test_version = monitor.test_id, monitor.test_version
            max_age, minimum = monitor.source_freshness_seconds, monitor.minimum_completeness

        age = (now - data.source_observed_at.astimezone(timezone.utc)).total_seconds()
        if age > max_age or data.source_completeness < minimum:
            reasons = []
            if age > max_age:
                reasons.append("source_freshness_below_threshold")
            if data.source_completeness < minimum:
                reasons.append("source_completeness_below_threshold")
            return self._suspend(tenant_id, monitor_id, data, reasons, now)
        if (data.test_request.test_id, data.test_request.version) != (test_id, test_version):
            raise MonitorError("monitor execution must use its pinned released test version")

        result = self.tests.run(tenant_id, data.test_request)
        with self.database.transaction() as session:
            monitor = session.get(ContinuousMonitor, monitor_id)
            monitor.status = "active"
            monitor.suspended_reason = None
            run = MonitorRun(
                monitor_run_id=new_id("mrun"),
                tenant_id=tenant_id,
                monitor_id=monitor_id,
                control_test_run_id=result.run_id,
                idempotency_key=data.test_request.idempotency_key,
                status="succeeded",
                source_observed_at=data.source_observed_at,
                source_completeness=data.source_completeness,
                conclusion=result.conclusion.value,
                exception_count=result.exception_count,
                alert_count=0,
                details_json={"result_manifest_hash": result.result_manifest_hash},
            )
            session.add(run)
            session.flush()
            budget = monitor.alert_budget
            if result.exception_count > monitor.exception_threshold:
                for exception in result.exceptions[:budget]:
                    window = int(now.timestamp()) // monitor.deduplication_window_seconds
                    dedup = hashlib.sha256(
                        f"{monitor_id}:{exception.exception_key}:{window}".encode()
                    ).hexdigest()
                    alert = session.scalar(
                        select(MonitorAlert).where(
                            MonitorAlert.tenant_id == tenant_id,
                            MonitorAlert.monitor_id == monitor_id,
                            MonitorAlert.deduplication_key == dedup,
                        )
                    )
                    if alert is None:
                        alert = MonitorAlert(
                            alert_id=new_id("mal"),
                            tenant_id=tenant_id,
                            monitor_id=monitor_id,
                            monitor_run_id=run.monitor_run_id,
                            deduplication_key=dedup,
                            exception_key=exception.exception_key,
                            status="review_pending",
                            first_seen_at=now,
                            last_seen_at=now,
                            details_json={
                                "severity": exception.severity,
                                "reason": exception.reason,
                            },
                        )
                        session.add(alert)
                    else:
                        alert.occurrence_count += 1
                        alert.last_seen_at = now
                        alert.monitor_run_id = run.monitor_run_id
                    run.alert_count += 1
            session.flush()
            return self._run_view(run)

    def _suspend(self, tenant_id, monitor_id, data, reasons, now):
        with self.database.transaction() as session:
            monitor = session.get(ContinuousMonitor, monitor_id)
            monitor.status = "suspended"
            monitor.suspended_reason = ",".join(reasons)
            run = MonitorRun(
                monitor_run_id=new_id("mrun"),
                tenant_id=tenant_id,
                monitor_id=monitor_id,
                idempotency_key=data.test_request.idempotency_key,
                status="suspended",
                source_observed_at=data.source_observed_at,
                source_completeness=data.source_completeness,
                conclusion="source_unreliable",
                details_json={"suspension_reasons": reasons, "evaluated_at": now.isoformat()},
            )
            session.add(run)
            session.flush()
            return self._run_view(run)

    def overview(self, tenant_id: str) -> dict[str, Any]:
        with self.database.read_session() as session:
            monitors = list(
                session.scalars(
                    select(ContinuousMonitor)
                    .where(ContinuousMonitor.tenant_id == tenant_id)
                    .order_by(ContinuousMonitor.monitor_key, ContinuousMonitor.version.desc())
                )
            )
            alerts = list(
                session.scalars(
                    select(MonitorAlert).where(
                        MonitorAlert.tenant_id == tenant_id, MonitorAlert.status != "resolved"
                    )
                )
            )
            return {
                "monitors": [self._monitor_view(item) for item in monitors],
                "open_alerts": [self._alert_view(item) for item in alerts],
            }

    @staticmethod
    def _monitor_view(row):
        return {
            "monitor_id": row.monitor_id,
            "monitor_key": row.monitor_key,
            "version": row.version,
            "title": row.title,
            "status": row.status,
            "test": f"{row.test_id}@{row.test_version}",
            "owner_ref": row.owner_ref,
            "reviewer_ref": row.reviewer_ref,
            "suspended_reason": row.suspended_reason,
        }

    @staticmethod
    def _run_view(row):
        return {
            "monitor_run_id": row.monitor_run_id,
            "monitor_id": row.monitor_id,
            "control_test_run_id": row.control_test_run_id,
            "status": row.status,
            "conclusion": row.conclusion,
            "exception_count": row.exception_count,
            "alert_count": row.alert_count,
            "details": row.details_json,
        }

    @staticmethod
    def _alert_view(row):
        return {
            "alert_id": row.alert_id,
            "monitor_id": row.monitor_id,
            "exception_key": row.exception_key,
            "status": row.status,
            "occurrence_count": row.occurrence_count,
            "review_case_ref": row.review_case_ref,
            "details": row.details_json,
        }
