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
    assert config["generation_config"]["model"].endswith("/gemini-3.6-flash")
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
    result = {
        "schema": "assurance.agent_engine_deployment_result.v1",
        "complete": True,
        "model": "gemini-3.6-flash",
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
            },
            {
                "agent_id": "agent-b",
                "package_sha256": "sha-b",
                "resource_name": (
                    "projects/1/locations/us-central1/reasoningEngines/engine-b"
                ),
            },
        ],
    }
    proof_path = tmp_path / "fleet.json"
    proof_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setenv("ASSURANCEOS_AGENT_ENGINE_PROOF", str(proof_path))

    proof = managed_fleet_proof(
        repository_root=tmp_path,
        expected_packages=packages,
        model="gemini-3.6-flash",
    )
    assert proof["cloud_verified"] is True
    assert proof["deployed_count"] == 2
    assert proof["memory_bank"]["configured"] is True
