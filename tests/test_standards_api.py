"""Standards and Audit Pack compilation over HTTP.

Two things are checked here that the service tests cannot: that registering a
pack and approving one are different permissions, and that an entitlement is read
from canonical state rather than accepted from the request body. An entitlement a
caller can assert is not an entitlement.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.security import ROLE_PERMISSIONS, Permission

TENANT = "tnt_packs"
ENGAGEMENT = "eng_packs"


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "packs-api.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id=TENANT, slug="packs", name="Packs"))
        session.flush()
        session.add(
            Engagement(
                engagement_id=ENGAGEMENT,
                tenant_id=TENANT,
                code="SCM-PACKS",
                title="SCM",
                status="planned",
                audit_pack_ref="pending",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
    monkeypatch.setattr(api, "database", database)
    try:
        yield TestClient(api.app)
    finally:
        database.dispose()


def compile_body(**overrides) -> dict:
    body = {
        "pack_id": "software-change-management",
        "version": "2.0.0",
        "entity_name": "Asteria Systems DemoCo",
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
        "in_scope_systems": ["github://asteria/api"],
    }
    body.update(overrides)
    return body


def admit(client, pack_id="software-change-management", version="2.0.0") -> None:
    registered = client.post(f"/api/v1/audit-packs/{pack_id}/versions/{version}/registration")
    assert registered.status_code == 200, registered.text
    approved = client.post(
        f"/api/v1/audit-packs/{pack_id}/versions/{version}/approval",
        json={"reason": "Methodology reviewed against the current policy version."},
    )
    assert approved.status_code == 200, approved.text


def test_the_registry_reports_what_each_pack_requires(client):
    packs = client.get("/api/v1/audit-packs").json()["packs"]
    by_id = {item["pack_id"]: item for item in packs}
    assert set(by_id) == {"software-change-management", "identity-access", "privileged-access"}
    assert by_id["software-change-management"]["requires_control_tests"] == ["SCM-01@2.0.0"]
    assert by_id["privileged-access"]["entitlement_required"] is True


def test_an_engagement_compiles_from_an_approved_pack(client):
    admit(client)
    compiled = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/compile", json=compile_body()
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["task_count"] == 11

    provenance = client.get(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/provenance"
    ).json()
    assert provenance["pack"] == "software-change-management@2.0.0"
    assert provenance["control_tests"] == {"SCM-01": "2.0.0"}


def test_an_unapproved_pack_is_a_conflict(client):
    client.post("/api/v1/audit-packs/identity-access/versions/1.0.0/registration")
    refused = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/compile",
        json=compile_body(pack_id="identity-access", version="1.0.0"),
    )
    assert refused.status_code == 409
    assert "only from an approved pack" in refused.json()["detail"]


def test_an_entitlement_cannot_be_asserted_by_the_caller(client):
    """403, and the request body has no field that could have avoided it.

    The compile endpoint reads entitlements from canonical state. If it accepted
    them, the licensing control would be a suggestion.
    """
    admit(client, "privileged-access", "1.0.0")
    refused = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/compile",
        json=compile_body(pack_id="privileged-access", version="1.0.0"),
    )
    assert refused.status_code == 403
    assert "holds no entitlement" in refused.json()["detail"]

    granted = client.post(
        f"/api/v1/tenants/{TENANT}/standard-entitlements",
        json={"standard_code": "SYN-PAM-BENCH", "licence_ref": "SUB-2026-0042"},
    )
    assert granted.status_code == 200, granted.text

    allowed = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/compile",
        json=compile_body(pack_id="privileged-access", version="1.0.0"),
    )
    assert allowed.status_code == 200, allowed.text


def test_criteria_outside_the_period_are_a_validation_error(client):
    admit(client)
    refused = client.post(
        f"/api/v1/tenants/{TENANT}/engagements/{ENGAGEMENT}/compile",
        json=compile_body(period_start="2025-01-01", period_end="2025-12-31"),
    )
    assert refused.status_code == 422
    assert "do not cover the audit period" in refused.json()["detail"]


def test_an_unknown_pack_is_a_404(client):
    assert (
        client.post("/api/v1/audit-packs/procure-to-pay/versions/1.0.0/registration").status_code
        == 404
    )


def test_registering_and_approving_a_pack_are_different_permissions():
    """Admitting an artefact and endorsing a methodology are different jobs.

    Auditors register; approvers approve. No non-admin role does both, so the
    separation holds through role membership and not only through a service check.
    """
    writers = {
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.STANDARDS_WRITE in permissions
    }
    approvers = {
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.STANDARDS_APPROVE in permissions
    }
    assert writers & approvers == {"admin"}
    assert Permission.STANDARDS_WRITE not in ROLE_PERMISSIONS["worker"]
    assert Permission.STANDARDS_READ in ROLE_PERMISSIONS["worker"]
