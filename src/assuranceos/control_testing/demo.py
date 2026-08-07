from __future__ import annotations

from datetime import date
from pathlib import Path

from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database

from .definitions import ControlTestDataset, ControlTestRunRequest
from .registry import ControlTestRegistry
from .service import ControlTestService

DEMO_TENANT = "tnt_asteria"


def build_service(database: Database, root: Path) -> ControlTestService:
    registry = ControlTestRegistry(
        root / "tests-library",
        trusted_public_key=(root / "security/release-keys/control-test-release-public.pem").read_bytes(),
    ).load()
    service = ControlTestService(database, registry)
    service.synchronize_registry(released_by="demo-release-pipeline")
    return service


def run_control_test_demo(database: Database, root: Path) -> dict:
    with database.transaction() as session:
        if TenantRepository(session).get(DEMO_TENANT) is None:
            TenantRepository(session).add(
                Tenant(tenant_id=DEMO_TENANT, slug="asteria", name="Asteria Systems DemoCo")
            )
    service = build_service(database, root)
    scm = service.run(
        DEMO_TENANT,
        ControlTestRunRequest(
            test_id="SCM-01",
            version="2.0.0",
            purpose="Golden SCM operating-effectiveness test",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            requested_by="usr_demo_auditor",
            idempotency_key="demo:scm-01:2026-07",
            parameters={"expected_population_count": 3, "required_approvals": 1},
            datasets=[
                ControlTestDataset(
                    name="pull_requests",
                    expected_count=3,
                    evidence_ids=["ev_demo_github_population"],
                    records=[
                        {"pull_request_id":"PR-1001","repository":"asteria/api","merged_at":"2026-07-04T10:00:00Z","approvals":1,"change_ticket":"CHG-1","exception_key":None,"evidence_id":"ev_pr_1001"},
                        {"pull_request_id":"PR-1002","repository":"asteria/api","merged_at":"2026-07-11T10:00:00Z","approvals":0,"change_ticket":"CHG-2","exception_key":None,"evidence_id":"ev_pr_1002"},
                        {"pull_request_id":"PR-1003","repository":"asteria/api","merged_at":"2026-07-18T10:00:00Z","approvals":0,"change_ticket":None,"exception_key":"EX-SVC","evidence_id":"ev_pr_1003"},
                    ],
                ),
                ControlTestDataset(
                    name="change_tickets",
                    evidence_ids=["ev_demo_jira_population"],
                    records=[
                        {"ticket_id":"CHG-1","status":"Approved","evidence_id":"ev_chg_1"},
                        {"ticket_id":"CHG-2","status":"Approved","evidence_id":"ev_chg_2"},
                    ],
                ),
                ControlTestDataset(
                    name="approved_exceptions",
                    evidence_ids=["ev_demo_exception_register"],
                    records=[{"exception_key":"EX-SVC","active":True,"evidence_id":"ev_ex_svc"}],
                ),
            ],
        ),
    )
    iam = service.run(
        DEMO_TENANT,
        ControlTestRunRequest(
            test_id="IAM-01",
            version="1.0.0",
            purpose="Golden terminated-user deprovisioning test",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            requested_by="usr_demo_auditor",
            idempotency_key="demo:iam-01:2026-07",
            parameters={"expected_population_count": 3},
            datasets=[
                ControlTestDataset(
                    name="terminated_users",
                    expected_count=3,
                    evidence_ids=["ev_demo_hr_terminations"],
                    records=[
                        {"user_id":"u-001","terminated_at":"2026-07-01T10:00:00Z","disable_due_at":"2026-07-01T14:00:00Z","evidence_id":"ev_term_1"},
                        {"user_id":"u-002","terminated_at":"2026-07-02T10:00:00Z","disable_due_at":"2026-07-02T14:00:00Z","evidence_id":"ev_term_2"},
                        {"user_id":"u-003","terminated_at":"2026-07-03T10:00:00Z","disable_due_at":"2026-07-03T14:00:00Z","evidence_id":"ev_term_3"},
                    ],
                ),
                ControlTestDataset(
                    name="directory_accounts",
                    evidence_ids=["ev_demo_directory_accounts"],
                    records=[
                        {"user_id":"u-001","enabled":False,"disabled_at":"2026-07-01T12:00:00Z","exception_key":None,"evidence_id":"ev_acc_1"},
                        {"user_id":"u-002","enabled":True,"disabled_at":None,"exception_key":None,"evidence_id":"ev_acc_2"},
                        {"user_id":"u-003","enabled":True,"disabled_at":None,"exception_key":"EX-IAM","evidence_id":"ev_acc_3"},
                    ],
                ),
                ControlTestDataset(
                    name="approved_exceptions",
                    evidence_ids=["ev_demo_iam_exceptions"],
                    records=[{"exception_key":"EX-IAM","active":True,"evidence_id":"ev_iam_ex"}],
                ),
            ],
        ),
    )
    return {
        "tenant_id": DEMO_TENANT,
        "released_tests": service.list_releases(),
        "runs": [scm.model_dump(mode="json"), iam.model_dump(mode="json")],
        "expected": {
            "SCM-01": {"conclusion": "ineffective", "exception_count": 1},
            "IAM-01": {"conclusion": "ineffective", "exception_count": 1},
        },
    }
