from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from assuranceos.managed_fleet import (
    managed_fleet_proof,
    memory_bank_config,
    persist_reviewed_session,
    tenant_memory_subject,
)


def test_memory_bank_config_is_gemini_36_revisioned_and_tenant_scoped():
    config = memory_bank_config(project="assurance-project", location="us-central1")
    assert config["generation_config"]["model"].endswith("/gemini-3.7-flash")
    assert config["similarity_search_config"]["embedding_model"].endswith(
        "/text-embedding-005"
    )
    assert config["customization_configs"][0]["scope_keys"] == ["user_id"]
    assert config["disable_memory_revisions"] is False


def test_memory_generation_requires_review_and_exact_tenant_subject():
    class App:
        def __init__(self):
            self.sessions = []

        async def async_add_session_to_memory(self, *, session):
            self.sessions.append(session)

    app = App()
    subject = tenant_memory_subject("tnt_a", "auditor_a")
    session = {
        "id": "session_1",
        "user_id": subject,
        "state": {"memory_review_status": "approved"},
    }
    receipt = asyncio.run(
        persist_reviewed_session(
            app,
            session=session,
            tenant_id="tnt_a",
            principal_id="auditor_a",
        )
    )
    assert receipt["generated"] is True
    assert app.sessions == [session]

    with pytest.raises(ValueError, match="approved"):
        asyncio.run(
            persist_reviewed_session(
                app,
                session={**session, "state": {}},
                tenant_id="tnt_a",
                principal_id="auditor_a",
            )
        )


def test_managed_fleet_proof_validates_resources_and_release_digests(
    tmp_path: Path, monkeypatch
):
    packages = {
        "agent-a": SimpleNamespace(release={"package_sha256": "sha-a"}),
        "agent-b": SimpleNamespace(release={"package_sha256": "sha-b"}),
    }
    template = "projects/assurance-project/locations/us-central1/templates/guardrails"
    result = {
        "schema": "assurance.agent_engine_deployment_result.v2",
        "complete": True,
        "model": "gemini-3.7-flash",
        "project": "assurance-project",
        "region": "us-central1",
        "deployed_at": "2026-08-08T00:00:00Z",
        "deployed": [
            {
                "agent_id": "agent-a",
                "package_sha256": "sha-a",
                "resource_name": (
                    "projects/1/locations/us-central1/reasoningEngines/engine-a"
                ),
                "identity_type": "AGENT_IDENTITY",
                "verified_at": "2026-08-08T00:01:00Z",
                "memory_bank": {"configured": True},
            },
            {
                "agent_id": "agent-b",
                "package_sha256": "sha-b",
                "resource_name": (
                    "projects/1/locations/us-central1/reasoningEngines/engine-b"
                ),
                "identity_type": "AGENT_IDENTITY",
                "verified_at": "2026-08-08T00:01:00Z",
                "memory_bank": {"configured": True},
            },
        ],
        "verification": {
            "method": "agentplatform.agent_engines.get",
            "verified_at": "2026-08-08T00:01:00Z",
            "resource_count": 2,
        },
        "managed_services": {
            "model_armor": {
                "schema": "assurance.model_armor_verification.v1",
                "template": template,
                "verified_at": "2026-08-08T00:01:00Z",
                "method": "modelarmor.sanitizeUserPrompt+sanitizeModelResponse",
                "safe_model_response": "NO_MATCH_FOUND",
                "adversarial_user_prompt": "MATCH_FOUND",
            }
        },
    }
    proof_path = tmp_path / "fleet.json"
    proof_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setenv("ASSURANCEOS_AGENT_ENGINE_PROOF", str(proof_path))
    monkeypatch.setenv("ASSURANCEOS_MODEL_ARMOR_TEMPLATE", template)

    proof = managed_fleet_proof(
        repository_root=tmp_path,
        expected_packages=packages,
        model="gemini-3.7-flash",
    )
    assert proof["cloud_verified"] is True
    assert proof["deployed_count"] == 2
    assert proof["memory_bank"]["configured"] is True
    assert proof["agent_identity"]["configured"] is True
    assert proof["model_armor"]["configured"] is True


def test_release_qualification_is_not_reported_as_a_cloud_deployment(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("ASSURANCEOS_AGENT_ENGINE_PROOF", raising=False)
    monkeypatch.delenv("ASSURANCEOS_AGENT_ENGINE_RESOURCE_MAP_JSON", raising=False)
    packages = {"agent-a": SimpleNamespace(release={"package_sha256": "sha-a"})}

    proof = managed_fleet_proof(
        repository_root=tmp_path,
        expected_packages=packages,
        model="gemini-3.7-flash",
    )

    assert proof["status"] == "release_qualified"
    assert proof["cloud_verified"] is False
    assert proof["memory_bank"]["configured"] is False
    assert proof["memory_bank"]["deployment_ready"] is True


def test_managed_fleet_rejects_duplicate_or_unread_deployment_receipts(
    tmp_path: Path, monkeypatch
):
    packages = {
        "agent-a": SimpleNamespace(release={"package_sha256": "sha-a"}),
        "agent-b": SimpleNamespace(release={"package_sha256": "sha-b"}),
    }
    item = {
        "agent_id": "agent-a",
        "package_sha256": "sha-a",
        "resource_name": "projects/1/locations/us-central1/reasoningEngines/engine-a",
        "identity_type": "AGENT_IDENTITY",
        "verified_at": "2026-08-08T00:01:00Z",
        "memory_bank": {"configured": True},
    }
    monkeypatch.setenv(
        "ASSURANCEOS_AGENT_ENGINE_RESOURCE_MAP_JSON",
        json.dumps(
            {
                "schema": "assurance.agent_engine_deployment_result.v2",
                "complete": True,
                "model": "gemini-3.7-flash",
                "project": "assurance-project",
                "region": "us-central1",
                "deployed": [item, item],
                "verification": {
                    "method": "create-response-only",
                    "verified_at": "2026-08-08T00:01:00Z",
                    "resource_count": 2,
                },
            }
        ),
    )

    proof = managed_fleet_proof(
        repository_root=tmp_path,
        expected_packages=packages,
        model="gemini-3.7-flash",
    )

    assert proof["status"] == "proof_invalid"
    assert proof["cloud_verified"] is False
    assert any("read-back" in error for error in proof["verification_errors"])
    assert any("duplicate" in error for error in proof["verification_errors"])
