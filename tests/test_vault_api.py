from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.vault import EvidenceVault


def test_evidence_http_contracts(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "api-vault.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id="tnt_api", slug="api", name="API Tenant")
        )
    vault = EvidenceVault.local(database, tmp_path / "objects")
    monkeypatch.setattr(api, "database", database)
    monkeypatch.setattr(api, "vault", vault)
    monkeypatch.setattr(
        api,
        "settings",
        replace(
            api.settings,
            evidence_export_root=tmp_path / "exports",
            max_evidence_upload_bytes=1024,
        ),
    )

    try:
        client = TestClient(api.app)
        acquired = client.post(
            "/api/v1/tenants/tnt_api/evidence",
            params={
                "source_type": "github",
                "source_locator": "github://org/repo/pull/7",
                "actor_id": "connector:github",
                "acquisition_key": "github:org/repo:pull:7:v1",
                "original_filename": "pull-7.json",
                "accepted": "true",
            },
            content=b'{"approved":true}',
            headers={"content-type": "application/json"},
        )
        assert acquired.status_code == 200
        original = acquired.json()
        assert original["record_kind"] == "original"
        assert original["integrity_status"] == "verified"

        derived = client.post(
            "/api/v1/tenants/tnt_api/evidence/derived",
            params=[
                ("source_evidence_id", original["evidence_id"]),
                ("operation", "redaction"),
                ("tool_version", "redactor@1"),
                ("actor_id", "evidence-custodian"),
            ],
            content=b'{"approved":"[REDACTED]"}',
            headers={"content-type": "application/json"},
        )
        assert derived.status_code == 200
        derivative = derived.json()
        assert derivative["record_kind"] == "derived"

        listed = client.get("/api/v1/tenants/tnt_api/evidence")
        assert listed.status_code == 200
        assert len(listed.json()) == 2

        downloaded = client.get(
            f"/api/v1/tenants/tnt_api/evidence/{original['evidence_id']}/content",
            params={"actor_id": "auditor", "purpose": "fieldwork"},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b'{"approved":true}'
        assert downloaded.headers["x-evidence-sha256"] == original["content_sha256"]

        custody = client.get(
            f"/api/v1/tenants/tnt_api/evidence/{original['evidence_id']}/custody"
        )
        assert custody.status_code == 200
        assert custody.json()["verification"]["valid"] is True

        lineage = client.get(
            f"/api/v1/tenants/tnt_api/evidence/{derivative['evidence_id']}/lineage"
        )
        assert lineage.status_code == 200
        assert len(lineage.json()["edges"]) == 1

        exported = client.post(
            "/api/v1/tenants/tnt_api/evidence-exports",
            json={
                "evidence_ids": [derivative["evidence_id"]],
                "actor_id": "auditor",
                "purpose": "external review",
                "include_ancestors": True,
            },
        )
        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/zip"
        package = tmp_path / "downloaded.zip"
        package.write_bytes(exported.content)
        verification = vault.verify_export(package)
        assert verification.valid is True
        assert verification.evidence_count == 2
    finally:
        database.dispose()


def test_evidence_upload_limit_is_enforced(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "api-limit.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id="tnt_api", slug="api", name="API Tenant")
        )
    monkeypatch.setattr(api, "database", database)
    monkeypatch.setattr(api, "vault", EvidenceVault.local(database, tmp_path / "objects"))
    monkeypatch.setattr(
        api,
        "settings",
        replace(api.settings, max_evidence_upload_bytes=4),
    )
    try:
        client = TestClient(api.app)
        response = client.post(
            "/api/v1/tenants/tnt_api/evidence",
            params={
                "source_type": "upload",
                "source_locator": "upload://too-large",
                "actor_id": "user",
            },
            content=b"12345",
        )
        assert response.status_code == 413
    finally:
        database.dispose()
