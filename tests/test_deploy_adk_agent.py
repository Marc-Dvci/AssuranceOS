from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.deploy_adk_agent import _agent_engine_config, _resource_name


def test_agent_engine_config_enables_managed_identity_and_memory_bank(tmp_path: Path):
    package = SimpleNamespace(
        path=tmp_path / "agent-a",
        agent_id="agent-a",
        manifest={"display_name": "Agent A", "mandate": "Test safely", "version": "1.0.0"},
        release={"package_sha256": "abc123"},
    )

    config = _agent_engine_config(
        package=package,
        model="gemini-3.7-flash",
        project="assurance-project",
        region="us-central1",
        staging_bucket="gs://agent-staging",
    )

    assert config["identity_type"] == "AGENT_IDENTITY"
    assert config["context_spec"]["memory_bank_config"]["disable_memory_revisions"] is False
    assert config["env_vars"]["ASSURANCEOS_AGENT_PACKAGE_SHA256"] == "abc123"
    assert config["staging_bucket"] == "gs://agent-staging"


def test_agent_engine_readback_name_supports_sdk_resource_shapes():
    assert _resource_name(SimpleNamespace(name="projects/p/locations/r/reasoningEngines/1"))
    assert _resource_name(SimpleNamespace(resource_name="resource-1")) == "resource-1"
    assert _resource_name(SimpleNamespace(api_resource=SimpleNamespace(name="resource-2"))) == (
        "resource-2"
    )
