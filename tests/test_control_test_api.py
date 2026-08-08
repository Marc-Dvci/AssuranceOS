from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.control_testing import ControlTestRegistry
from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database

ROOT = Path(__file__).resolve().parents[1]


def payload() -> dict:
    return {
        "test_id": "SCM-01",
        "version": "2.0.0",
        "purpose": "API contract test",
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
        "idempotency_key": "api-scm-run",
        "parameters": {"expected_population_count": 1, "required_approvals": 1},
        "datasets": [
            {
                "name": "pull_requests",
                "evidence_ids": ["ev_prs"],
                "expected_count": 1,
                "records": [
                    {
                        "pull_request_id": "PR-1",
                        "repository": "asteria/api",
                        "merged_at": "2026-07-04T10:00:00Z",
                        "approvals": 1,
                        "change_ticket": "CHG-1",
                        "exception_key": None,
                        "evidence_id": "ev_pr1",
                    }
                ],
            },
            {
                "name": "change_tickets",
                "evidence_ids": ["ev_tickets"],
                "records": [
                    {"ticket_id": "CHG-1", "status": "Approved", "evidence_id": "ev_chg1"}
                ],
            },
            {
                "name": "approved_exceptions",
                "evidence_ids": ["ev_exceptions"],
                "records": [],
            },
        ],
    }


def test_control_test_http_contracts(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "control-test-api.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_api", slug="api", name="API"))
    registry = ControlTestRegistry(
        ROOT / "tests-library",
        trusted_public_key=(
            ROOT / "security/release-keys/control-test-release-public.pem"
        ).read_bytes(),
    ).load()
    monkeypatch.setattr(api, "database", database)
    monkeypatch.setattr(api, "control_test_registry", registry)
    from assuranceos.control_testing import ControlTestService
    ControlTestService(database, registry).synchronize_registry()
    try:
        client = TestClient(api.app)
        releases = client.get("/api/v1/control-tests")
        assert releases.status_code == 200
        assert {item["test_id"] for item in releases.json()} == {"SCM-01", "IAM-01", "SLA-01"}

        execution = client.post("/api/v1/tenants/tnt_api/control-test-runs", json=payload())
        assert execution.status_code == 200, execution.text
        body = execution.json()
        assert body["conclusion"] == "effective"
        assert body["exception_count"] == 0

        fetched = client.get(
            f"/api/v1/tenants/tnt_api/control-test-runs/{body['run_id']}"
        )
        assert fetched.status_code == 200
        assert fetched.json()["result_manifest_hash"] == body["result_manifest_hash"]

        reproduction = payload()
        reproduction.pop("test_id")
        reproduction.pop("version")
        reproduction.pop("idempotency_key")
        verified = client.post(
            f"/api/v1/tenants/tnt_api/control-test-runs/{body['run_id']}/verify-reproducibility",
            json=reproduction,
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["reproducible"] is True
    finally:
        database.dispose()
