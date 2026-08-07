
from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.connectors import ConnectorService
from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.vault import EvidenceVault


def test_connector_registry_and_grant_http_contracts(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "connector-api.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id="tnt_api", slug="connector-api", name="Connector API")
        )
    vault = EvidenceVault.local(database, tmp_path / "objects")
    service = ConnectorService(database, vault)
    monkeypatch.setattr(api, "database", database)
    monkeypatch.setattr(api, "vault", vault)
    monkeypatch.setattr(api, "connector_service", service)

    try:
        client = TestClient(api.app)
        created = client.post(
            "/api/v1/tenants/tnt_api/connectors",
            json={
                "connector_key": "github-main",
                "connector_type": "github",
                "display_name": "GitHub Main",
                "base_url": "https://api.github.test",
                "credential_ref": "secret://github/main",
                "config": {"organization": "asteria"},
            },
        )
        assert created.status_code == 200
        connector = created.json()
        assert connector["credential_ref"] == "secret://github/main"
        assert "secret-value" not in created.text

        listed = client.get("/api/v1/tenants/tnt_api/connectors")
        assert listed.status_code == 200
        assert [item["connector_key"] for item in listed.json()] == ["github-main"]

        grant_response = client.post(
            f"/api/v1/tenants/tnt_api/connectors/{connector['connector_instance_id']}/grants",
            json={
                "grant_key": "scm-audit",
                "purpose": "Collect pull requests for the approved SCM audit",
                "allowed_streams": ["pull_requests"],
                "resource_selectors": {"repositories": ["asteria/platform"]},
                "approved_by": "audit-owner",
            },
        )
        assert grant_response.status_code == 200
        grant = grant_response.json()
        assert grant["read_only"] is True
        assert grant["status"] == "active"

        grants = client.get(
            "/api/v1/tenants/tnt_api/collection-grants",
            params={"connector_instance_id": connector["connector_instance_id"]},
        )
        assert grants.status_code == 200
        assert len(grants.json()) == 1

        revoked = client.post(
            f"/api/v1/tenants/tnt_api/collection-grants/{grant['grant_id']}/revoke",
            json={"actor_id": "audit-owner", "reason": "audit completed"},
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"

        missing = client.get("/api/v1/tenants/tnt_api/connector-runs/run_missing")
        assert missing.status_code == 404
    finally:
        database.dispose()
