from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from assuranceos.control_testing import (
    ControlTestDataset,
    ControlTestRegistry,
    ControlTestRunRequest,
    ControlTestService,
)
from assuranceos.control_testing.exceptions import (
    ReproducibilityMismatchError,
    TestInputValidationError as InputValidationError,
    TestPackageError as PackageError,
)
from assuranceos.db.models import ControlTestRun, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = (ROOT / "security/release-keys/control-test-release-public.pem").read_bytes()


def registry(root: Path = ROOT / "tests-library") -> ControlTestRegistry:
    return ControlTestRegistry(root, trusted_public_key=PUBLIC_KEY).load()


@pytest.fixture
def service(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "control-tests.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_test", slug="test", name="Test"))
    result = ControlTestService(database, registry())
    assert result.synchronize_registry() == 2
    try:
        yield result, database
    finally:
        database.dispose()


def scm_request(*, expected_count: int = 3, key: str = "scm-run") -> ControlTestRunRequest:
    return ControlTestRunRequest(
        test_id="SCM-01",
        version="2.0.0",
        purpose="Test approved changes before merge",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        requested_by="usr_auditor",
        idempotency_key=key,
        parameters={"expected_population_count": expected_count, "required_approvals": 1},
        datasets=[
            ControlTestDataset(
                name="pull_requests",
                evidence_ids=["ev_prs"],
                expected_count=expected_count,
                records=[
                    {"pull_request_id":"PR-1001","repository":"asteria/api","merged_at":"2026-07-04T10:00:00Z","approvals":1,"change_ticket":"CHG-1","exception_key":None,"evidence_id":"ev_pr1"},
                    {"pull_request_id":"PR-1002","repository":"asteria/api","merged_at":"2026-07-11T10:00:00Z","approvals":0,"change_ticket":"CHG-2","exception_key":None,"evidence_id":"ev_pr2"},
                    {"pull_request_id":"PR-1003","repository":"asteria/api","merged_at":"2026-07-18T10:00:00Z","approvals":0,"change_ticket":None,"exception_key":"EX-1","evidence_id":"ev_pr3"},
                ],
            ),
            ControlTestDataset(
                name="change_tickets",
                evidence_ids=["ev_tickets"],
                records=[
                    {"ticket_id":"CHG-1","status":"Approved","evidence_id":"ev_chg1"},
                    {"ticket_id":"CHG-2","status":"Approved","evidence_id":"ev_chg2"},
                ],
            ),
            ControlTestDataset(
                name="approved_exceptions",
                evidence_ids=["ev_exceptions"],
                records=[{"exception_key":"EX-1","active":True,"evidence_id":"ev_ex1"}],
            ),
        ],
    )


def iam_request(*, key: str = "iam-run") -> ControlTestRunRequest:
    return ControlTestRunRequest(
        test_id="IAM-01",
        version="1.0.0",
        purpose="Test terminated user deprovisioning",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        requested_by="usr_auditor",
        idempotency_key=key,
        parameters={"expected_population_count": 3},
        datasets=[
            ControlTestDataset(
                name="terminated_users",
                evidence_ids=["ev_terms"],
                expected_count=3,
                records=[
                    {"user_id":"u-001","terminated_at":"2026-07-01T10:00:00Z","disable_due_at":"2026-07-01T14:00:00Z","evidence_id":"ev_t1"},
                    {"user_id":"u-002","terminated_at":"2026-07-02T10:00:00Z","disable_due_at":"2026-07-02T14:00:00Z","evidence_id":"ev_t2"},
                    {"user_id":"u-003","terminated_at":"2026-07-03T10:00:00Z","disable_due_at":"2026-07-03T14:00:00Z","evidence_id":"ev_t3"},
                ],
            ),
            ControlTestDataset(
                name="directory_accounts",
                evidence_ids=["ev_directory"],
                records=[
                    {"user_id":"u-001","enabled":False,"disabled_at":"2026-07-01T12:00:00Z","exception_key":None,"evidence_id":"ev_a1"},
                    {"user_id":"u-002","enabled":True,"disabled_at":None,"exception_key":None,"evidence_id":"ev_a2"},
                    {"user_id":"u-003","enabled":True,"disabled_at":None,"exception_key":"EX-IAM","evidence_id":"ev_a3"},
                ],
            ),
            ControlTestDataset(
                name="approved_exceptions",
                evidence_ids=["ev_iam_ex"],
                records=[{"exception_key":"EX-IAM","active":True,"evidence_id":"ev_ie1"}],
            ),
        ],
    )


def test_registry_loads_signed_python_and_sql_releases():
    releases = registry().list()
    assert [(r.manifest.test_id, r.manifest.engine) for r in releases] == [
        ("IAM-01", "sql"),
        ("SCM-01", "python"),
    ]
    assert all(r.release_document["package_sha256"] for r in releases)


def test_registry_rejects_tampered_package(tmp_path: Path):
    import shutil

    target = tmp_path / "tests-library"
    shutil.copytree(ROOT / "tests-library", target)
    test_file = target / "scm/approved-change-before-merge/test.py"
    test_file.write_text(test_file.read_text() + "\n# tamper\n")
    with pytest.raises(PackageError, match="file manifest does not match"):
        registry(target)


def test_python_control_test_executes_full_population_and_persists_manifest(service):
    subject, database = service
    result = subject.run("tnt_test", scm_request())
    assert result.status == "succeeded"
    assert result.conclusion == "ineffective"
    assert result.population_count == 3
    assert result.sampled_count == 3
    assert result.population_complete is True
    assert result.exception_count == 1
    assert result.exceptions[0].exception_key == "SCM-01:asteria/api:PR-1002"
    assert result.exceptions[0].evidence_ids == ["ev_pr2", "ev_chg2"]
    assert len(result.input_manifest_hash) == len(result.result_manifest_hash) == 64
    with database.read_session() as session:
        row = session.scalar(select(ControlTestRun).where(ControlTestRun.run_id == result.run_id))
        assert row is not None and row.execution_environment_json["runtime"] == "isolated-python-subprocess"


def test_sql_control_test_executes_and_excludes_approved_exception(service):
    subject, _ = service
    result = subject.run("tnt_test", iam_request())
    assert result.status == "succeeded"
    assert result.conclusion == "ineffective"
    assert result.exception_count == 1
    assert result.exceptions[0].exception_key == "IAM-01:u-002"
    approved = next(row for row in result.rows if row["user_id"] == "u-003")
    assert approved["classification"] == "approved_exception"


def test_incomplete_population_blocks_execution(service):
    subject, _ = service
    result = subject.run("tnt_test", scm_request(expected_count=4, key="blocked"))
    assert result.status == "blocked"
    assert result.conclusion == "population_incomplete"
    assert result.population_complete is False
    assert result.execution_manifest_hash
    assert result.limitations


def test_duplicate_population_keys_fail_closed(service):
    subject, _ = service
    request = scm_request(key="duplicates")
    request.datasets[0].records.append(copy.deepcopy(request.datasets[0].records[0]))
    request.parameters["expected_population_count"] = 4
    request.datasets[0].expected_count = 4
    result = subject.run("tnt_test", request)
    assert result.status == "blocked"
    assert "duplicate population keys" in result.limitations[0]


def test_idempotency_returns_same_run_and_rejects_different_inputs(service):
    subject, _ = service
    first = subject.run("tnt_test", scm_request(key="same"))
    second = subject.run("tnt_test", scm_request(key="same"))
    assert second.run_id == first.run_id
    changed = scm_request(key="same")
    changed.datasets[0].records[0]["approvals"] = 9
    with pytest.raises(InputValidationError, match="different test inputs"):
        subject.run("tnt_test", changed)


def test_reproducibility_verification_detects_input_change(service):
    subject, _ = service
    request = scm_request(key="reproduce")
    result = subject.run("tnt_test", request)
    verified = subject.verify_reproducibility("tnt_test", result.run_id, request)
    assert verified.reproducible is True
    changed = request.model_copy(deep=True)
    changed.datasets[0].records[0]["approvals"] = 2
    with pytest.raises(ReproducibilityMismatchError, match="inputs do not match"):
        subject.verify_reproducibility("tnt_test", result.run_id, changed)


def test_schema_contains_control_test_tables(service):
    _, database = service
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "control_test_releases",
        "control_test_runs",
        "control_test_dataset_bindings",
        "control_test_exceptions",
    }.issubset(tables)


def test_registry_domain_filter_and_missing_release():
    subject = registry()
    assert [item.manifest.test_id for item in subject.list(domain="iam")] == ["IAM-01"]
    from assuranceos.control_testing.exceptions import TestReleaseNotFoundError
    with pytest.raises(TestReleaseNotFoundError):
        subject.get("NOPE", "1.0.0")


def test_python_static_policy_rejects_unapproved_import_and_file_access(tmp_path: Path):
    source = tmp_path / "bad.py"
    source.write_text("import os\nopen('/tmp/x')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unapproved Python imports"):
        ControlTestRegistry._validate_python_source(source, allowed_libraries=set())
    source.write_text("open('/tmp/x')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prohibited Python call"):
        ControlTestRegistry._validate_python_source(source, allowed_libraries=set())


def test_deterministic_hash_sampling_is_stable_and_order_independent():
    from dataclasses import replace
    from assuranceos.control_testing.definitions import SamplingPolicy
    from assuranceos.control_testing.service import ControlTestService

    release = registry().get("SCM-01", "2.0.0")
    manifest = release.manifest.model_copy(
        update={
            "sampling": SamplingPolicy(
                method="deterministic_hash", default_size=2, default_seed="seed"
            )
        }
    )
    sampled_release = replace(release, manifest=manifest)
    records = [{"id": value} for value in range(10)]
    first = ControlTestService._sample(sampled_release, records, {})
    second = ControlTestService._sample(sampled_release, list(reversed(records)), {})
    assert first == second
    assert len(first) == 2


def test_strict_canonical_evidence_references_fail_closed(service):
    subject, database = service
    strict = ControlTestService(database, subject.registry, require_canonical_evidence=True)
    with pytest.raises(InputValidationError, match="not canonical"):
        strict.run("tnt_test", scm_request(key="strict-evidence"))


def test_engagement_and_task_references_are_tenant_scoped(service):
    subject, _ = service
    request = scm_request(key="bad-engagement")
    request.engagement_id = "eng_missing"
    with pytest.raises(InputValidationError, match="engagement does not exist"):
        subject.run("tnt_test", request)


def test_execution_failure_is_persisted(service):
    from assuranceos.control_testing.exceptions import TestExecutionError
    from assuranceos.control_testing.runtime import DeterministicRuntime

    class FailingRuntime(DeterministicRuntime):
        def execute(self, *args, **kwargs):
            raise TestExecutionError("deliberate runtime failure")

    subject, database = service
    failing = ControlTestService(database, subject.registry, runtime=FailingRuntime())
    request = scm_request(key="runtime-failure")
    with pytest.raises(TestExecutionError, match="deliberate"):
        failing.run("tnt_test", request)
    with database.read_session() as session:
        row = session.scalar(
            select(ControlTestRun).where(ControlTestRun.idempotency_key == "runtime-failure")
        )
        assert row is not None
        assert row.status == "failed"
        assert row.conclusion == "test_failed_technically"
        assert row.failure_class == "TestExecutionError"


def test_orchestrator_worker_adapter_returns_canonical_run_reference(service):
    from assuranceos.control_testing.worker import ControlTestTaskHandler
    from assuranceos.db.models import Engagement, EngagementTask
    from assuranceos.orchestration.definitions import TaskLease
    from datetime import datetime, timedelta, timezone

    subject, database = service
    with database.transaction() as session:
        session.add(
            Engagement(
                engagement_id="eng_control_test",
                tenant_id="tnt_test",
                code="CONTROL-TEST",
                title="Control test",
                audit_pack_ref="scm@1",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
        session.add(
            EngagementTask(
                task_id="tsk_control_test",
                tenant_id="tnt_test",
                engagement_id="eng_control_test",
                task_key="run-scm",
                task_type="deterministic_test",
                definition_version="1",
                status="running",
                idempotency_key="task:control-test",
            )
        )
    handler = ControlTestTaskHandler(
        subject,
        lambda lease: scm_request(key=f"worker:{lease.task_id}"),
    )
    lease = TaskLease(
        tenant_id="tnt_test",
        engagement_id="eng_control_test",
        task_id="tsk_control_test",
        task_key="run-scm",
        task_type="deterministic_test",
        attempt_count=1,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    output = handler(lease)
    assert output.output_refs[0].startswith("control-test-run:tst_")
    assert output.result["exception_count"] == 1
