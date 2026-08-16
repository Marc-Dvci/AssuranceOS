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
        wheel=tmp_path / "assuranceos-0.8.0-py3-none-any.whl",
    )

    assert config["identity_type"] == "AGENT_IDENTITY"
    assert config["context_spec"]["memory_bank_config"]["disable_memory_revisions"] is False
    assert config["env_vars"]["ASSURANCEOS_AGENT_PACKAGE_SHA256"] == "abc123"
    assert config["staging_bucket"] == "gs://agent-staging"

    # The managed runtime installs `assuranceos` from a wheel rather than
    # receiving `src/assuranceos` as a directory. Shipping the directory created
    # the engine and then killed it on `ModuleNotFoundError`, so the wheel has to
    # appear in both places: uploaded, and named as something pip installs.
    assert "assuranceos-0.8.0-py3-none-any.whl" in " ".join(config["extra_packages"])
    installed = [x for x in config["requirements"] if x.endswith(".whl")]
    assert len(installed) == 1
    assert installed[0].endswith("assuranceos-0.8.0-py3-none-any.whl")
    # cloudpickle is what the SDK serialises the agent with, and it refuses to
    # create anything when the deployment does not declare it.
    assert any(x.startswith("cloudpickle") for x in config["requirements"])


def test_agent_engine_readback_name_supports_sdk_resource_shapes():
    assert _resource_name(SimpleNamespace(name="projects/p/locations/r/reasoningEngines/1"))
    assert _resource_name(SimpleNamespace(resource_name="resource-1")) == "resource-1"
    assert _resource_name(SimpleNamespace(api_resource=SimpleNamespace(name="resource-2"))) == (
        "resource-2"
    )


def test_wheel_requirement_resolves_where_the_builder_extracts_it(tmp_path: Path):
    """The wheel requirement must carry no `user_code/` prefix.

    The Agent Engine builder copies the uploaded objects to `/code/user_code/`
    and then runs `tar -xvf user_code/dependencies.tar.gz` from `/code`, so
    `extra_packages` land at `/code/<relative path>` while pip runs from
    `/code`. A prefixed requirement points one directory too deep and the build
    fails with "No such file or directory".
    """

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
        wheel=Path("assuranceos-0.8.0-py3-none-any.whl"),
    )

    wheel_requirements = [r for r in config["requirements"] if r.endswith(".whl")]
    assert wheel_requirements == ["assuranceos-0.8.0-py3-none-any.whl"]
    assert not any(r.startswith("user_code/") for r in config["requirements"])
    # Whatever the requirement names must be something the deploy actually uploads.
    assert wheel_requirements[0] in config["extra_packages"]
