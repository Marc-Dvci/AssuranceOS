"""The sandbox over HTTP: who may reach it, and what it still refuses.

The module tests cover the rules. These cover the boundary: that the routes are
absent unless the deployment enabled them, that the evaluator credential is
enough and an anonymous caller is not, and that nothing on these paths can be
turned against the demonstration tenant.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from assuranceos import api
from assuranceos.db.session import Database
from assuranceos.security import JwtVerifier, Permission, ROLE_PERMISSIONS
from assuranceos.vault import EvidenceVault

SECRET = "sandbox-test-secret-with-more-than-thirty-two-bytes"


def _token(*, roles: list[str], tenant_ids: list[str]) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "evaluator@devpost.example",
            "iss": "https://issuer.example",
            "aud": "assuranceos",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "roles": roles,
            "tenant_ids": tenant_ids,
        },
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A client on a database of this test's own.

    Two things here are the point rather than housekeeping.

    The database is created here instead of being whatever the process happens
    to be configured with. Reading the developer's database made this file pass
    on a laptop that had run the product and fail on a machine that had not,
    which is the worse kind of green: the suite disagreeing with itself across
    machines about code that never changed.

    And the application state is swapped *inside* the try. Setting it before,
    with only the ``yield`` guarded, means a fixture that raises during setup
    leaves the whole process demanding JWT authentication -- so one broken test
    in this file turned every other API test in the suite red, for a reason
    none of them had anything to do with.
    """

    previous_settings = api.app.state.settings
    previous_verifier = api.app.state.jwt_verifier
    database = Database.from_sqlite_path(tmp_path / "api.db")
    database.create_schema()
    try:
        monkeypatch.setattr(api, "database", database)
        monkeypatch.setattr(
            api, "vault", EvidenceVault.local(database, tmp_path / "objects")
        )
        # `Settings` is frozen on purpose, so the switch is flipped by replacing
        # the value the module resolves rather than by mutating it.
        monkeypatch.setattr(
            api, "settings", replace(api.settings, evaluator_sandbox_enabled=True)
        )
        api.app.state.settings = SimpleNamespace(auth_mode="jwt")
        api.app.state.jwt_verifier = JwtVerifier(
            issuer="https://issuer.example",
            audience="assuranceos",
            algorithms=("HS256",),
            secret=SECRET,
        )
        api._sandbox_instance.cache_clear()
        yield TestClient(api.app)
    finally:
        api._sandbox_instance.cache_clear()
        api.app.state.settings = previous_settings
        api.app.state.jwt_verifier = previous_verifier
        database.dispose()


@pytest.fixture()
def evaluator() -> dict[str, str]:
    token = _token(roles=["viewer"], tenant_ids=["tnt_asteria_demo"])
    return {"Authorization": f"Bearer {token}"}


def test_the_evaluator_role_carries_the_sandbox_permission():
    assert Permission.SANDBOX_OPERATE in ROLE_PERMISSIONS["viewer"]
    # And it is not a licence over the demonstration tenant.
    assert Permission.CONNECTOR_WRITE not in ROLE_PERMISSIONS["viewer"]
    assert Permission.EVIDENCE_WRITE not in ROLE_PERMISSIONS["viewer"]
    assert Permission.DEMO_OPERATE not in ROLE_PERMISSIONS["viewer"]


def test_an_anonymous_caller_cannot_create_a_workspace(client: TestClient):
    response = client.post(
        "/api/v1/evaluator-sandbox/workspaces", json={"company_name": "Anonymous Ltd"}
    )
    assert response.status_code == 401


def test_the_routes_are_absent_unless_the_deployment_enabled_them(
    client: TestClient, evaluator: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        api, "settings", replace(api.settings, evaluator_sandbox_enabled=False)
    )
    response = client.get("/api/v1/evaluator-sandbox/providers", headers=evaluator)
    assert response.status_code == 404
    assert "not enabled" in response.json()["detail"]


def test_the_catalogue_offers_every_provider_and_states_the_limits(
    client: TestClient, evaluator: dict[str, str]
):
    body = client.get("/api/v1/evaluator-sandbox/providers", headers=evaluator).json()
    assert {item["connector_type"] for item in body["providers"]} == {
        "github",
        "jira",
        "confluence",
        "google_drive",
        "okta",
        "entra",
        "gcp_iam",
    }
    assert body["limits"]["max_objects"] >= 1
    assert body["credential_storage"]


def test_a_workspace_round_trips_and_is_deletable(client: TestClient, evaluator: dict[str, str]):
    created = client.post(
        "/api/v1/evaluator-sandbox/workspaces",
        headers=evaluator,
        json={"company_name": "Evaluator Test Ltd", "primary_domain": "example.com"},
    )
    assert created.status_code == 200
    workspace = created.json()
    assert workspace["tenant_id"] == f"tnt_eval_{workspace['workspace_id']}"

    fetched = client.get(
        f"/api/v1/evaluator-sandbox/workspaces/{workspace['workspace_id']}", headers=evaluator
    )
    assert fetched.status_code == 200
    assert fetched.json()["connectors"] == []

    deleted = client.delete(
        f"/api/v1/evaluator-sandbox/workspaces/{workspace['workspace_id']}", headers=evaluator
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_a_sandbox_route_cannot_be_aimed_at_the_demonstration_tenant(
    client: TestClient, evaluator: dict[str, str]
):
    """The evaluator token names ``tnt_asteria_demo`` and still cannot reach it here.

    This is the assertion the whole separation rests on. The token legitimately
    carries the demonstration tenant, because the evaluator is meant to read it;
    what must not follow is any ability to write to it through a route whose
    authority was granted for a workspace.
    """

    for identifier in ("tnt_asteria_demo", "asteria", "../tnt_asteria_demo"):
        response = client.get(
            f"/api/v1/evaluator-sandbox/workspaces/{identifier}", headers=evaluator
        )
        assert response.status_code == 404, identifier


def test_a_caller_cannot_choose_the_credential_reference(
    client: TestClient, evaluator: dict[str, str]
):
    """``credential_ref`` is not an accepted field, and the schema says so.

    ``extra="forbid"`` is what turns that from a convention into a refusal, so
    the request is rejected rather than quietly ignored.
    """

    workspace = client.post(
        "/api/v1/evaluator-sandbox/workspaces",
        headers=evaluator,
        json={"company_name": "Reference Ltd"},
    ).json()
    response = client.post(
        f"/api/v1/evaluator-sandbox/workspaces/{workspace['workspace_id']}/connectors",
        headers=evaluator,
        json={
            "provider": "github",
            "base_url": "https://api.github.com",
            "stream": "pull_requests",
            "scope": "octocat/hello-world",
            "credential_ref": "gcp-secret://projects/audit-505613/secrets/db-password/versions/1",
        },
    )
    assert response.status_code == 422


def test_a_provider_url_outside_the_allowlist_is_refused_over_http(
    client: TestClient, evaluator: dict[str, str]
):
    workspace = client.post(
        "/api/v1/evaluator-sandbox/workspaces",
        headers=evaluator,
        json={"company_name": "Allowlist Ltd"},
    ).json()
    response = client.post(
        f"/api/v1/evaluator-sandbox/workspaces/{workspace['workspace_id']}/connectors",
        headers=evaluator,
        json={
            "provider": "github",
            "base_url": "https://169.254.169.254",
            "stream": "pull_requests",
            "scope": "octocat/hello-world",
        },
    )
    assert response.status_code == 400
    assert "allowlist" in response.json()["detail"]


def test_the_audit_route_needs_the_sandbox_permission(client: TestClient):
    response = client.post(
        "/api/v1/evaluator-sandbox/workspaces/" + "0" * 32 + "/audit",
        json={"connector_instance_id": "con_x", "repository": "owner/repo"},
    )
    assert response.status_code == 401


def test_the_audit_route_refuses_an_unknown_workspace(
    client: TestClient, evaluator: dict[str, str]
):
    response = client.post(
        "/api/v1/evaluator-sandbox/workspaces/tnt_asteria_demo/audit",
        headers=evaluator,
        json={"connector_instance_id": "con_x", "repository": "owner/repo"},
    )
    assert response.status_code == 404


def test_the_audit_route_refuses_a_repository_that_is_not_owner_slash_repo(
    client: TestClient, evaluator: dict[str, str]
):
    """The refusal has to arrive before anything reaches the network.

    That is the assertion, not a detail of it: this test makes no outbound call
    and would fail by timing out or by rate limit if the ordering regressed, so
    it is checking the ordering rather than only the message.
    """

    workspace = client.post(
        "/api/v1/evaluator-sandbox/workspaces",
        headers=evaluator,
        json={"company_name": "Malformed Ltd"},
    ).json()
    attached = client.post(
        f"/api/v1/evaluator-sandbox/workspaces/{workspace['workspace_id']}/connectors",
        headers=evaluator,
        json={
            "provider": "github",
            "base_url": "https://api.github.com",
            "stream": "pull_requests",
            "scope": "owner/repo",
        },
    )
    assert attached.status_code == 200, attached.text
    response = client.post(
        f"/api/v1/evaluator-sandbox/workspaces/{workspace['workspace_id']}/audit",
        headers=evaluator,
        json={
            "connector_instance_id": attached.json()["connector"]["connector_instance_id"],
            "repository": "not-a-repository",
            "period_start": "2026-08-01",
            "period_end": "2026-08-10",
        },
    )
    assert response.status_code == 400
    assert "owner/repository" in response.json()["detail"]
