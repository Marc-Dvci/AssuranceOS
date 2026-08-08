from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from assuranceos.control_testing import ControlTestRunRequest, TestConclusion as Conclusion
from assuranceos.db.models import ContinuousMonitor, Finding, MonitorAlert, MonitorRun, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.monitoring import (
    ContinuousMonitoringService,
    MonitorDefinitionInput,
    MonitorExecutionInput,
)


NOW = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)


class FakeRegistry:
    def get(self, test_id, version):
        assert (test_id, version) == ("IAM-001", "1.0.0")
        return object()


class FakeTests:
    def __init__(self):
        self.registry = FakeRegistry()
        self.calls = 0

    def run(self, tenant_id, request):
        self.calls += 1
        return SimpleNamespace(
            run_id=None,
            conclusion=Conclusion.INEFFECTIVE,
            exception_count=1,
            exceptions=[
                SimpleNamespace(
                    exception_key="principal:admin@example.test",
                    severity="high",
                    reason="Standing administrative access",
                )
            ],
            result_manifest_hash="a" * 64,
        )


def _request(key: str) -> ControlTestRunRequest:
    return ControlTestRunRequest(
        test_id="IAM-001",
        version="1.0.0",
        purpose="Continuous privileged-access assurance",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 8),
        requested_by="monitor-worker",
        idempotency_key=key,
        datasets=[],
    )


def _definition() -> MonitorDefinitionInput:
    return MonitorDefinitionInput(
        monitor_key="privileged-access",
        title="Standing privileged access",
        test_id="IAM-001",
        test_version="1.0.0",
        owner_ref="audit-automation@example.test",
        reviewer_ref="audit-reviewer@example.test",
        source_freshness_seconds=3600,
        minimum_completeness=0.99,
        exception_threshold=0,
        deduplication_window_seconds=86400,
        alert_budget=10,
        response_sla_seconds=86400,
        independence_preserved=True,
        approval_ref="approval-41",
        approved_by="chief-audit-executive@example.test",
    )


def _service(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "monitoring.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt-a", slug="a", name="A"))
    tests = FakeTests()
    return database, tests, ContinuousMonitoringService(database, tests, clock=lambda: NOW)


def test_monitor_suspends_before_execution_when_source_quality_degrades(tmp_path):
    database, tests, service = _service(tmp_path)
    monitor = service.activate("tnt-a", _definition())
    result = service.execute(
        "tnt-a",
        monitor["monitor_id"],
        MonitorExecutionInput(
            source_observed_at=NOW - timedelta(hours=2),
            source_completeness=0.98,
            test_request=_request("monitor-stale"),
        ),
    )
    assert result["status"] == "suspended"
    assert set(result["details"]["suspension_reasons"]) == {
        "source_freshness_below_threshold",
        "source_completeness_below_threshold",
    }
    assert tests.calls == 0
    assert service.overview("tnt-a")["monitors"][0]["status"] == "suspended"
    database.dispose()


def test_monitor_deduplicates_alerts_and_never_creates_a_finding(tmp_path):
    database, _, service = _service(tmp_path)
    monitor = service.activate("tnt-a", _definition())
    for key in ("monitor-run-1", "monitor-run-2"):
        result = service.execute(
            "tnt-a",
            monitor["monitor_id"],
            MonitorExecutionInput(
                source_observed_at=NOW - timedelta(minutes=5),
                source_completeness=1,
                test_request=_request(key),
            ),
        )
        assert result["alert_count"] == 1

    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(MonitorRun)) == 2
        alert = session.scalar(select(MonitorAlert))
        assert alert is not None and alert.occurrence_count == 2
        assert alert.status == "review_pending"
        assert session.scalar(select(func.count()).select_from(Finding)) == 0
    database.dispose()


def test_new_monitor_version_retires_prior_active_configuration(tmp_path):
    database, _, service = _service(tmp_path)
    first = service.activate("tnt-a", _definition())
    second = service.activate(
        "tnt-a", _definition().model_copy(update={"approval_ref": "approval-42"})
    )
    assert (first["version"], second["version"]) == (1, 2)
    with database.read_session() as session:
        statuses = list(
            session.scalars(select(ContinuousMonitor.status).order_by(ContinuousMonitor.version))
        )
    assert statuses == ["retired", "active"]
    database.dispose()
