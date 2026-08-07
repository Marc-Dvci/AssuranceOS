"""The finding lifecycle over HTTP.

Two things are checked here that the service tests cannot: that the lifecycle is
reachable only through transitions — there is no endpoint that sets a status —
and that the human gate is mirrored in the permission model, so the role agents
run under cannot reach the decision endpoint at all.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.security import Permission, ROLE_PERMISSIONS

TENANT = "tnt_api"
ENGAGEMENT = "eng_api"


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "api.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id=TENANT, slug="api", name="API")
        )
        session.flush()
        session.add(
            Engagement(
                engagement_id=ENGAGEMENT,
                tenant_id=TENANT,
                code="SCM-API",
                title="SCM",
                status="in_progress",
                audit_pack_ref="software-change-management@1.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
    monkeypatch.setattr(api, "database", database)
    try:
        yield TestClient(api.app)
    finally:
        database.dispose()


def proposal(**overrides) -> dict:
    body = {
        "finding": {
            "code": "SCM-01",
            "title": "Changes merged without an approved ticket",
            "severity": "high",
            "confidence": 0.8,
            "criteria": "Change policy v4 requires an approved ticket.",
            "observed_condition": "2 exception(s) identified: PR-1002, PR-1003",
            "risk_statement": "Unauthorised change may reach production.",
            "evidence_ids": ["ev_changes"],
            "exception_keys": ["PR-1002", "PR-1003"],
        },
        "exception_rows": [
            {"exception_key": "PR-1002", "subject_ref": "PR-1002"},
            {"exception_key": "PR-1003", "subject_ref": "PR-1003"},
        ],
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
    }
    body.update(overrides)
    return body


def test_the_whole_lifecycle_runs_over_http(client):
    proposed = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["supported"] is True
    finding_id = proposed.json()["finding_id"]

    decided = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Confirmed against the change register.",
            "idempotency_key": "approve-1",
            "actor_id": "alice.auditor@asteria.example",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"

    opened = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation",
        json={
            "owner_ref": "platform-team@asteria.example",
            "due_date": "2026-10-31",
            "action_plan": "Enforce the ticket in the merge gate.",
            "idempotency_key": "rem-1",
            "external_system": "jira",
        },
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["created"] is True
    action_id = opened.json()["action_id"]

    closure = client.post(
        f"/api/v1/tenants/{TENANT}/remediation-actions/{action_id}/closure",
        json={
            "response_text": "Merge gate updated.",
            "closure_evidence_ids": ["ev_gate"],
            "submitted_by": "platform-team@asteria.example",
        },
    )
    assert closure.status_code == 200, closure.text

    retest = client.post(
        f"/api/v1/tenants/{TENANT}/remediation-actions/{action_id}/retests",
        json={
            "procedure_ref": "SCM-01@2.0.0",
            "idempotency_key": "rt-1",
            "outcome": "closed_verified",
            "evidence_ids": ["ev_august"],
            "performed_by": "bob.retester@asteria.example",
        },
    )
    assert retest.status_code == 200, retest.text
    assert retest.json()["status"] == "closed_verified"

    view = client.get(f"/api/v1/tenants/{TENANT}/findings/{finding_id}")
    assert view.status_code == 200
    assert view.json()["status"] == "closed_verified"
    assert view.json()["retests"][0]["performed_by"] == "bob.retester@asteria.example"


def test_replayed_remediation_returns_the_same_action(client):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Confirmed.",
            "idempotency_key": "approve-1",
            "actor_id": "alice.auditor@asteria.example",
        },
    )
    body = {
        "owner_ref": "platform-team@asteria.example",
        "due_date": "2026-10-31",
        "action_plan": "Enforce the ticket in the merge gate.",
        "idempotency_key": "rem-1",
    }
    first = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation", json=body
    ).json()
    second = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation", json=body
    ).json()

    assert first["action_id"] == second["action_id"]
    assert first["created"] is True and second["created"] is False


def test_a_governance_refusal_is_not_a_validation_error(client):
    """403, not 422. The request was fine; the system declined the effect."""
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]

    refused = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Looks right to me.",
            "idempotency_key": "approve-1",
            "actor_id": "agent:quality-reviewer",
        },
    )
    assert refused.status_code == 403
    assert "attributable to a person" in refused.json()["detail"]


def test_remediation_before_approval_is_a_conflict(client):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]

    early = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation",
        json={
            "owner_ref": "platform-team@asteria.example",
            "due_date": "2026-10-31",
            "action_plan": "Too early.",
            "idempotency_key": "rem-1",
        },
    )
    assert early.status_code == 409
    assert "cannot move to" in early.json()["detail"]


def test_a_finding_with_no_evidence_is_rejected_at_the_boundary(client):
    body = proposal()
    body["finding"]["evidence_ids"] = []
    response = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=body
    )
    assert response.status_code == 422


def test_a_skeptic_rejected_finding_reports_its_contradictions(client):
    body = proposal(
        approved_exceptions=[
            {"subject_ref": "PR-1002", "reference": "EXC-1"},
            {"subject_ref": "PR-1003", "reference": "EXC-2"},
        ]
    )
    response = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=body
    )
    assert response.status_code == 200
    assert response.json()["supported"] is False
    kinds = {item["kind"] for item in response.json()["contradictions"]}
    assert kinds == {"approved_exception"}


def test_an_unknown_finding_is_a_404(client):
    assert client.get(f"/api/v1/tenants/{TENANT}/findings/fnd_nope").status_code == 404


# -- the human gate in the permission model ------------------------------------


def test_only_approver_and_admin_may_adjudicate():
    """The gate is mirrored in authorization, not only in the service.

    The worker role is what agent execution runs under. It may propose a finding
    and must not be able to decide one, so the separation holds even if a caller
    reaches the endpoint with a valid worker token.
    """
    allowed = {
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.FINDING_ADJUDICATE in permissions
    }
    assert allowed == {"approver", "admin"}

    assert Permission.FINDING_WRITE in ROLE_PERMISSIONS["worker"]
    assert Permission.FINDING_ADJUDICATE not in ROLE_PERMISSIONS["worker"]
    assert Permission.FINDING_ADJUDICATE not in ROLE_PERMISSIONS["auditor"]
