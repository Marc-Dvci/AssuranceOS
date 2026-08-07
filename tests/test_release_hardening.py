from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from conftest import alembic_head as current_head

from assuranceos.agent_release import verify_agent_release
from assuranceos.audit_pack_release import verify_audit_pack_release
from assuranceos.connectors.adapters.github import GitHubPullRequestConnector
from assuranceos.connectors.credentials import CredentialResolver
from assuranceos.connectors.definitions import ConnectorInstanceView
from assuranceos.connectors.factory import ConnectorFactory
from assuranceos.registry import AgentRegistry
from assuranceos.security import JwtVerifier


def _token(*, tenant_ids: list[str], roles: list[str], secret: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "usr_release_test",
            "iss": "https://issuer.example",
            "aud": "assuranceos",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "tenant_ids": tenant_ids,
            "roles": roles,
            "jti": "release-test-token",
        },
        secret,
        algorithm="HS256",
    )


def test_management_api_requires_jwt_and_enforces_tenant_scope():
    from assuranceos import api

    secret = "release-test-secret-with-more-than-thirty-two-bytes"
    previous_settings = api.app.state.settings
    previous_verifier = api.app.state.jwt_verifier
    api.app.state.settings = SimpleNamespace(auth_mode="jwt")
    api.app.state.jwt_verifier = JwtVerifier(
        issuer="https://issuer.example",
        audience="assuranceos",
        algorithms=("HS256",),
        secret=secret,
    )
    try:
        client = TestClient(api.app)
        assert client.get("/api/v1/agents").status_code == 401

        token = _token(tenant_ids=["tnt_a"], roles=["viewer"], secret=secret)
        headers = {"Authorization": f"Bearer {token}"}
        agents = client.get("/api/v1/agents", headers=headers)
        assert agents.status_code == 200
        assert len(agents.json()) == 19

        denied = client.get("/api/v1/tenants/tnt_b/schedules", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["detail"] == "tenant access denied"
    finally:
        api.app.state.settings = previous_settings
        api.app.state.jwt_verifier = previous_verifier


def test_all_agent_packages_are_release_signed_and_tampering_is_detected(tmp_path: Path):
    root = Path("agents")
    packages = AgentRegistry(root).load()
    assert len(packages) == 19
    assert all(package.release["schema"] == "assurance.agent_release.v1" for package in packages.values())

    source = root / "skeptic"
    tampered = tmp_path / "skeptic"
    shutil.copytree(source, tampered)
    (tampered / "system_prompt.md").write_text(
        (tampered / "system_prompt.md").read_text(encoding="utf-8") + "\nUNSIGNED CHANGE\n",
        encoding="utf-8",
    )
    public_key = Path("security/release-keys/agent-release-public.pem").read_bytes()
    with pytest.raises(ValueError, match="file manifest does not match"):
        verify_agent_release(tampered, public_key)



def test_audit_pack_is_release_signed_and_tampering_is_detected(tmp_path: Path):
    source = Path("audit-packs/software-change-management")
    public_key = Path("security/release-keys/agent-release-public.pem").read_bytes()
    release = verify_audit_pack_release(source, public_key)
    assert release["pack_id"] == "software-change-management"
    assert release["version"] == "1.0.0"

    tampered = tmp_path / "software-change-management"
    shutil.copytree(source, tampered)
    with (tampered / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("\nUNSIGNED CHANGE\n")
    with pytest.raises(ValueError, match="file manifest does not match"):
        verify_audit_pack_release(tampered, public_key)

def test_connector_factory_builds_only_supported_credentialed_adapters(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GITHUB_HEADERS", json.dumps({"Authorization": "Bearer secret"}))
    transport = object()
    factory = ConnectorFactory(
        CredentialResolver(),
        transport_factory=lambda: transport,  # type: ignore[arg-type]
    )
    instance = ConnectorInstanceView(
        connector_instance_id="con_1",
        tenant_id="tnt_a",
        connector_key="github-primary",
        connector_type="github",
        display_name="GitHub",
        base_url="https://api.github.com",
        status="active",
        credential_ref="env://GITHUB_HEADERS",
        config={"api_version": "2022-11-28"},
        last_health_status=None,
        last_health_checked_at=None,
        last_health_details={},
    )
    connector = factory.build(instance)
    assert isinstance(connector, GitHubPullRequestConnector)
    assert connector.transport is transport
    assert "secret" not in repr(connector.credential)

    missing_credential = instance.model_copy(update={"credential_ref": None})
    with pytest.raises(ValueError, match="credential_ref"):
        factory.build(missing_credential)

    unsupported = instance.model_copy(update={"connector_type": "sap"})
    with pytest.raises(ValueError, match="unsupported live connector type"):
        factory.build(unsupported)


def test_production_hardening_migration_upgrades_populated_component5_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    database_path = tmp_path / "component5-to-polished.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ASSURANCEOS_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "0005_connector_sdk")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(tenant_id, slug, name, status, region, created_at, updated_at) "
                    "VALUES ('tnt_existing', 'existing', 'Existing Tenant', 'active', NULL, "
                    "'2026-08-06 12:00:00', '2026-08-06 12:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(outbox_id, tenant_id, aggregate_type, aggregate_id, event_type, "
                    "occurred_at, payload_json, idempotency_key, published_at, publish_attempts, "
                    "last_error) VALUES "
                    "('out_existing', 'tnt_existing', 'tenant', 'tnt_existing', "
                    "'tenant.seeded', '2026-08-06 12:00:00', '{}', "
                    "'tenant.seeded:tnt_existing', NULL, 0, NULL)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "task_attempts" in tables
        outbox_columns = {column["name"] for column in inspect(engine).get_columns("outbox_events")}
        assert {"available_at", "lease_owner", "dead_lettered_at"}.issubset(outbox_columns)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT available_at, publish_attempts FROM outbox_events "
                    "WHERE outbox_id='out_existing'"
                )
            ).one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert str(row.available_at).startswith("2026-08-06 12:00:00")
        assert row.publish_attempts == 0
        assert version == current_head()
    finally:
        engine.dispose()


def test_worker_claim_api_issues_authenticated_signed_execution_envelope():
    from assuranceos import api
    from assuranceos.execution_authority import ExecutionAuthority
    from assuranceos.execution_security import Ed25519ExecutionEnvelopeSigner
    from assuranceos.orchestration import TaskLease
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    secret = "release-test-secret-with-more-than-thirty-two-bytes"
    now = datetime.now(timezone.utc)

    class FakeOrchestrator:
        def claim_next(self, **kwargs):
            assert kwargs["worker_id"] == "worker_release_test"
            return TaskLease(
                tenant_id="tnt_a",
                engagement_id="eng_a",
                task_id="tsk_a",
                task_key="skeptic-review",
                task_type="agent",
                assigned_agent_role="skeptic",
                attempt_count=1,
                lease_owner="worker_release_test",
                lease_expires_at=now + timedelta(minutes=5),
                execution_policy={
                    "purpose": "Challenge the proposed observation",
                    "allowed_tools": ["evidence.query"],
                    "allowed_evidence_scopes": ["github:asteria/*"],
                },
                model_policy="audit-high-reasoning-v4",
                deadline_at=now + timedelta(hours=1),
            )

    previous_settings = api.app.state.settings
    previous_verifier = api.app.state.jwt_verifier
    previous_orchestrator = api.orchestrator
    previous_authority = api.execution_authority
    api.app.state.settings = SimpleNamespace(auth_mode="jwt")
    api.app.state.jwt_verifier = JwtVerifier(
        issuer="https://issuer.example",
        audience="assuranceos",
        algorithms=("HS256",),
        secret=secret,
    )
    api.orchestrator = FakeOrchestrator()  # type: ignore[assignment]
    api.execution_authority = ExecutionAuthority(
        AgentRegistry(Path("agents")).load(),
        Ed25519ExecutionEnvelopeSigner(Ed25519PrivateKey.generate(), "test-control-plane"),
    )
    try:
        client = TestClient(api.app)
        # Bind the worker identity to the verified JWT subject.
        token = jwt.encode(
            {
                "sub": "worker_release_test",
                "iss": "https://issuer.example",
                "aud": "assuranceos",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "tenant_ids": ["tnt_a"],
                "roles": ["worker"],
            },
            secret,
            algorithm="HS256",
        )
        response = client.post(
            "/api/v1/tenants/tnt_a/tasks/claim",
            headers={"Authorization": f"Bearer {token}"},
            json={"worker_id": "worker_release_test", "lease_seconds": 60},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["lease"]["task_id"] == "tsk_a"
        assert payload["signed_execution_envelope"]["schema"] == (
            "assurance.signed_execution_envelope.v1"
        )
        assert payload["signed_execution_envelope"]["envelope"]["lease_owner"] == (
            "worker_release_test"
        )
    finally:
        api.app.state.settings = previous_settings
        api.app.state.jwt_verifier = previous_verifier
        api.orchestrator = previous_orchestrator
        api.execution_authority = previous_authority
