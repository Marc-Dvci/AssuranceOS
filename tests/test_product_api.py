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


def test_every_response_carries_the_baseline_security_headers(product_client):
    """A guard is only worth having if it fails when it is removed.

    Asserting the exact policy rather than its presence, because a policy that
    quietly loses `frame-ancestors` still looks like a Content-Security-Policy
    to any check that only tests for the header's existence.
    """
    for route in ("/", "/judge", f"/api/v1/tenants/{TENANT_ID}/cockpit"):
        headers = product_client.get(route).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cross-origin-opener-policy"] == "same-origin"
        policy = headers["content-security-policy"]
        for directive in (
            "default-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "object-src 'none'",
            "connect-src 'self'",
            "form-action 'self'",
        ):
            assert directive in policy, f"{route} lost {directive!r}"


def test_transport_security_is_asserted_only_over_tls(product_client):
    """Claiming HTTPS-only on a plaintext local runtime would be a false claim."""
    plain = product_client.get("/")
    assert "strict-transport-security" not in plain.headers

    behind_proxy = product_client.get("/", headers={"x-forwarded-proto": "https"})
    assert behind_proxy.headers["strict-transport-security"].startswith("max-age=31536000")


def test_the_frontend_is_self_contained(product_client):
    """No external origin, so the policy above can name none.

    The font is embedded rather than fetched. If that ever regresses to a CDN
    link the page silently falls back to a system font behind a corporate proxy,
    and the Content-Security-Policy blocks it outright.
    """
    body = product_client.get("/").text
    assert "@font-face" in body
    assert "src:url(data:font/woff2;base64," in body
    for external in ("https://fonts.googleapis.com", "https://fonts.gstatic.com", "//cdn."):
        assert external not in body


def test_the_economics_route_is_scoped_to_the_programme_by_default(product_client):
    response = product_client.get(f"/api/v1/tenants/{TENANT_ID}/economics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "programme"
    assert payload["cost"]["priced_as"] == "gemini-3.7-flash"
    assert payload["comparison"]["headcount"] == 4
    assert "planning assumption" in payload["comparison"]["assumption"]


def test_the_economics_route_never_hides_an_unmetered_basis(product_client):
    """The caveat is part of the payload, not something a caller may add.

    A cost figure computed from a scripted client's word counts looks exactly
    like one measured against a model. The distinction has to travel with the
    number or it will be dropped by whoever renders it.
    """
    payload = product_client.get(f"/api/v1/tenants/{TENANT_ID}/economics").json()
    assert payload["measurement"] in {"metered", "scripted", "mixed", "none"}
    if payload["measurement"] != "metered":
        assert payload["caveat"]
        assert payload["comparison"]["equivalent_runs"] is None


def test_the_agent_catalogue_publishes_what_each_agent_will_not_do(product_client):
    response = product_client.get("/api/v1/agents/catalogue")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["agents"] == 19
    assert len(payload["domains"]) > 1
    for entry in payload["agents"]:
        assert entry["mandate"]
        assert entry["non_goals"]
        assert entry["known_limitations"]
