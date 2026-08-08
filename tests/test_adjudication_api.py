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
from assuranceos.adjudication import TicketRef
from assuranceos.db.models import ConnectorInstance, Engagement, Tenant
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


def clear_gates(client, finding_id, reviewer="carol.qa@asteria.example") -> dict:
    """Score materiality and pass the quality review, as an engagement would.

    Approval sits behind both. The cases below are about the lifecycle over HTTP;
    the gates themselves are exercised separately further down.
    """
    scored = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/materiality",
        json={
            "inputs": {"population_size": 40, "exception_count": 2},
            "assessed_by": "agent:finding-adjudicator",
        },
    )
    assert scored.status_code == 200, scored.text
    reviewed = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/quality-review",
        json={"reviewer_id": reviewer, "notes": "Support traced."},
    )
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def test_the_whole_lifecycle_runs_over_http(client):
    proposed = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["supported"] is True
    finding_id = proposed.json()["finding_id"]

    assert clear_gates(client, finding_id)["passed"] is True

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
    clear_gates(client, finding_id)
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


def test_review_and_approval_are_different_permissions():
    """Preparer, reviewer and approver are three roles, not one with three verbs.

    The service refuses a reviewer who also approves, but that check only fires
    once someone has reached both endpoints. Keeping the permissions disjoint
    means no single non-admin role can reach both in the first place.
    """
    reviewers = {
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.FINDING_REVIEW in permissions
    }
    approvers = {
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.FINDING_ADJUDICATE in permissions
    }
    assert reviewers & approvers == {"admin"}
    # An agent runs as `worker`. It may write a finding and must not be able to
    # pass its own work through the methodology gate.
    assert Permission.FINDING_REVIEW not in ROLE_PERMISSIONS["worker"]


def test_management_may_dispute_and_nothing_else():
    permissions = ROLE_PERMISSIONS["business_owner"]
    assert Permission.FINDING_DISPUTE in permissions
    assert Permission.FINDING_WRITE not in permissions
    assert Permission.FINDING_REVIEW not in permissions
    assert Permission.FINDING_ADJUDICATE not in permissions


# -- the gates in front of approval --------------------------------------------


def test_approval_is_refused_before_the_gates(client):
    """403, and the reply names every blocker rather than the first."""
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]

    refused = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Confirmed against the change register.",
            "idempotency_key": "approve-1",
            "actor_id": "alice.auditor@asteria.example",
        },
    )
    assert refused.status_code == 403
    detail = refused.json()["detail"]
    assert "no materiality assessment exists" in detail
    assert "no passing quality review exists" in detail


def test_the_view_reports_what_is_blocking_approval(client):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]

    before = client.get(f"/api/v1/tenants/{TENANT}/findings/{finding_id}").json()
    assert before["approval_ready"] is False
    assert len(before["approval_blockers"]) == 2

    clear_gates(client, finding_id)
    after = client.get(f"/api/v1/tenants/{TENANT}/findings/{finding_id}").json()
    assert after["approval_ready"] is True
    assert after["approval_blockers"] == []


def test_editing_the_finding_spends_the_review_it_already_passed(client):
    """A severity override moves the content hash, so the review no longer applies.

    This is the case a status column cannot express: the finding really was
    reviewed, and the text that was reviewed is not the text on the record.
    """
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    scored = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/materiality",
        json={
            "inputs": {
                "population_size": 40,
                "exception_count": 2,
                "factors": [
                    {
                        "factor": "regulatory_reportable",
                        "rationale": "In scope for the operational-resilience regime.",
                        "evidence_ids": ["ev_scope"],
                    }
                ],
            },
            "assessed_by": "agent:finding-adjudicator",
        },
    )
    assert scored.json()["severity_floor"] == "high"
    client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/quality-review",
        json={"reviewer_id": "carol.qa@asteria.example"},
    )
    assert client.get(f"/api/v1/tenants/{TENANT}/findings/{finding_id}").json()[
        "approval_ready"
    ]

    lowered = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/severity-override",
        json={
            "severity": "medium",
            "reason": "Compensating detective control covers the exposure window.",
            "actor_id": "dana.director@asteria.example",
        },
    )
    assert lowered.status_code == 200, lowered.text

    view = client.get(f"/api/v1/tenants/{TENANT}/findings/{finding_id}").json()
    assert view["approval_ready"] is False
    assert view["approval_blockers"] == [
        "the finding changed after its last passing quality review"
    ]
    assert view["quality_reviews"][0]["applies_to_current_text"] is False


def test_an_override_that_does_not_lower_the_severity_is_refused(client):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/materiality",
        json={"inputs": {"population_size": 40, "exception_count": 2}},
    )

    refused = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/severity-override",
        json={
            "severity": "critical",
            "reason": "I would like this to be more serious than it is.",
            "actor_id": "dana.director@asteria.example",
        },
    )
    assert refused.status_code == 422
    assert "records a reduction" in refused.json()["detail"]


def test_a_disputed_finding_cannot_reach_remediation(client):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    clear_gates(client, finding_id)
    client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Confirmed against the change register.",
            "idempotency_key": "approve-1",
            "actor_id": "alice.auditor@asteria.example",
        },
    )
    disputed = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/disputes",
        json={
            "ground": "severity_overstated",
            "statement": "Two in forty is not a high-severity control failure.",
            "raised_by": "platform-team@asteria.example",
        },
    )
    assert disputed.status_code == 200, disputed.text
    dispute_id = disputed.json()["dispute_id"]

    blocked = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation",
        json={
            "owner_ref": "platform-team@asteria.example",
            "due_date": "2026-10-31",
            "action_plan": "Enforce the ticket in the merge gate.",
            "idempotency_key": "rem-1",
        },
    )
    assert blocked.status_code == 409

    resolved = client.post(
        f"/api/v1/tenants/{TENANT}/disputes/{dispute_id}/resolution",
        json={
            "resolution": "upheld",
            "reason": "The severity floor follows from reportability, not the count.",
            "resolved_by": "dana.director@asteria.example",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["finding_status"] == "approved"

    opened = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation",
        json={
            "owner_ref": "platform-team@asteria.example",
            "due_date": "2026-10-31",
            "action_plan": "Enforce the ticket in the merge gate.",
            "idempotency_key": "rem-1",
        },
    )
    assert opened.status_code == 200, opened.text


def test_the_party_that_raised_a_dispute_cannot_resolve_it(client):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    dispute_id = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/disputes",
        json={
            "ground": "condition_inaccurate",
            "statement": "The population includes merges from a different repository.",
            "raised_by": "platform-team@asteria.example",
        },
    ).json()["dispute_id"]

    refused = client.post(
        f"/api/v1/tenants/{TENANT}/disputes/{dispute_id}/resolution",
        json={
            "resolution": "withdrawn",
            "reason": "We contested it, so we will also close it in our favour.",
            "resolved_by": "platform-team@asteria.example",
        },
    )
    assert refused.status_code == 403
    assert "raised this dispute and cannot resolve it" in refused.json()["detail"]


def test_filing_a_ticket_without_a_configured_writer_is_a_bad_gateway(client):
    """A remediation registered against Jira is not quietly filed nowhere."""
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    clear_gates(client, finding_id)
    client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Confirmed against the change register.",
            "idempotency_key": "approve-1",
            "actor_id": "alice.auditor@asteria.example",
        },
    )
    action_id = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation",
        json={
            "owner_ref": "platform-team@asteria.example",
            "due_date": "2026-10-31",
            "action_plan": "Enforce the ticket in the merge gate.",
            "idempotency_key": "rem-1",
            "external_system": "jira",
            "external_target": "AUD",
        },
    ).json()["action_id"]

    refused = client.post(
        f"/api/v1/tenants/{TENANT}/remediation-actions/{action_id}/ticket"
    )
    assert refused.status_code == 502
    assert "exactly one active jira connector; found 0" in refused.json()["detail"]


def test_filing_a_ticket_resolves_the_tenant_connector(client, monkeypatch):
    finding_id = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/findings", json=proposal()
    ).json()["finding_id"]
    clear_gates(client, finding_id)
    client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/decisions",
        json={
            "decision": "approve",
            "reason": "Confirmed against the change register.",
            "idempotency_key": "approve-writer",
            "actor_id": "alice.auditor@asteria.example",
        },
    )
    action_id = client.post(
        f"/api/v1/tenants/{TENANT}/findings/{finding_id}/remediation",
        json={
            "owner_ref": "platform-team@asteria.example",
            "due_date": "2026-10-31",
            "action_plan": "Enforce the ticket in the merge gate.",
            "idempotency_key": "rem-writer",
            "external_system": "jira",
            "external_target": "AUD",
        },
    ).json()["action_id"]
    with api.database.transaction() as session:
        session.add(
            ConnectorInstance(
                connector_instance_id="con_jira",
                tenant_id=TENANT,
                connector_key="jira-remediation",
                connector_type="jira",
                display_name="Jira remediation",
                base_url="https://jira.example",
                status="active",
                credential_ref="env://JIRA_HEADERS",
                config_json={"issue_type": "Audit Finding"},
                last_health_details_json={},
            )
        )

    selected = []

    class StubWriter:
        system = "jira"

        def create_or_get(self, request):
            return TicketRef(
                system="jira",
                external_ref="AUD-41",
                url="https://jira.example/browse/AUD-41",
                created=True,
            )

    def build(instance):
        selected.append(instance)
        return StubWriter()

    monkeypatch.setattr(api, "writer_from_connector", build)
    response = client.post(
        f"/api/v1/tenants/{TENANT}/remediation-actions/{action_id}/ticket"
    )
    assert response.status_code == 200, response.text
    assert response.json()["external_ref"] == "AUD-41"
    assert selected[0].connector_key == "jira-remediation"
