from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.db.session import Database
from assuranceos.demo import TENANT_ID, run_golden_engagement
from assuranceos.ledger import AuditLedger


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def product_client(tmp_path, monkeypatch):
    database = Database.from_sqlite_path(tmp_path / "product.db")
    database.create_schema()
    run_golden_engagement(ROOT / "demo" / "asteria", AuditLedger(database))
    monkeypatch.setattr(api, "database", database)
    try:
        yield TestClient(api.app)
    finally:
        database.dispose()


def test_product_routes_serve_the_same_real_application(product_client):
    for route in (
        "/",
        "/plan-proposals",
        "/audits",
        "/findings",
        "/evidence",
        "/standards",
        "/governance",
        "/reporting",
        "/judge",
    ):
        response = product_client.get(route)
        assert response.status_code == 200
        assert "AssuranceOS" in response.text
        assert "Run golden audit" in response.text


def test_cockpit_is_tenant_scoped_and_source_backed(product_client):
    cockpit = product_client.get(f"/api/v1/tenants/{TENANT_ID}/cockpit")
    assert cockpit.status_code == 200
    payload = cockpit.json()
    assert payload["tenant_id"] == TENANT_ID
    assert [item["finding_id"] for item in payload["findings"]] == ["fnd_scm_001"]
    assert payload["findings"][0]["evidence_ids"]
    assert payload["metrics"]["open_findings"] == 1
    assert payload["governance"]["event_count"] > 0

    other = product_client.get("/api/v1/tenants/tnt_other/cockpit").json()
    assert other["findings"] == []
    assert other["evidence"] == []


def test_judge_overview_reads_signed_registries_and_deployment(product_client):
    response = product_client.get("/api/v1/judge/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fleet"]["agent_count"] == 19
    assert payload["fleet"]["released_count"] == 19
    assert len(payload["components"]) >= 10
    assert payload["release"]["commit"]
    components = {item["name"]: item for item in payload["components"]}
    assert components["Managed Agent Engine fleet"]["status"] == "attention"
    assert components["Memory Bank"]["status"] == "attention"
    assert components["Agent Runtime"]["status"] == "attention"


def test_prompt_injection_proof_replays_the_published_source(product_client):
    response = product_client.post("/api/v1/judge/proofs/prompt-injection")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "change_management_policy.md"
    assert payload["tainted"] is True
    assert payload["instruction_neutralized"] is True
    assert payload["canonical_state_mutated"] is False
    assert {item["category"] for item in payload["detectors"]} == {"prompt_injection"}


def test_trace_navigation_fails_closed_for_unknown_trace(product_client):
    response = product_client.get(f"/api/v1/tenants/{TENANT_ID}/traces/not-a-trace")
    assert response.status_code == 404
