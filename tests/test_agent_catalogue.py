from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.product import agent_catalogue
from assuranceos.registry import AgentRegistry


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def catalogue() -> dict:
    return agent_catalogue(AgentRegistry(ROOT / "agents").load())


def test_every_signed_agent_appears(catalogue):
    assert catalogue["totals"]["agents"] == 19
    assert catalogue["totals"]["released"] == 19


def test_each_entry_says_what_it_is_for_and_where_it_stops(catalogue):
    for entry in catalogue["agents"]:
        # A catalogue that lists only capabilities invites a department to
        # assume everything it does not mention.
        assert entry["mandate"], entry["agent_id"]
        assert entry["non_goals"], entry["agent_id"]
        assert entry["accountable_owner"], entry["agent_id"]
        assert entry["permitted_callers"], entry["agent_id"]


def test_the_mandate_is_the_signed_manifest_not_a_restatement(catalogue):
    """The catalogue must not describe an agent more generously than the artefact."""
    packages = AgentRegistry(ROOT / "agents").load()
    for entry in catalogue["agents"]:
        manifest = packages[entry["agent_id"]].manifest
        assert entry["mandate"] == manifest["mandate"]
        assert entry["non_goals"] == list(manifest["non_goals"])
        assert entry["human_gates"] == list(manifest.get("human_gates") or [])


def test_a_writing_tool_makes_the_agent_not_read_only(catalogue):
    by_id = {entry["agent_id"]: entry for entry in catalogue["agents"]}
    for entry in by_id.values():
        writes = [tool["name"] for tool in entry["tools"] if tool["writes"]]
        assert entry["read_only"] is (not writes), entry["agent_id"]
    # The distinction has to actually separate the fleet, or the field is
    # decoration: an assertion that only ever sees one value proves nothing.
    read_only = catalogue["totals"]["read_only"]
    assert 0 < read_only < catalogue["totals"]["agents"]


def test_known_limitations_come_from_the_package_file(catalogue):
    for entry in catalogue["agents"]:
        assert entry["known_limitations"], entry["agent_id"]
        source = (ROOT / "agents" / entry["agent_id"] / "known_limitations.md").read_text(
            encoding="utf-8"
        )
        for bullet in entry["known_limitations"]:
            assert bullet in source


def test_every_entry_carries_its_release_identity(catalogue):
    for entry in catalogue["agents"]:
        release = entry["release"]
        assert release["package_sha256"]
        assert release["prompt_hash"]
        assert release["release_key_id"]
        assert release["reviewers"]


def test_domains_partition_the_fleet(catalogue):
    counted = sum(row["agents"] for row in catalogue["domains"])
    assert counted == catalogue["totals"]["agents"]
    # A taxonomy that puts everything in one bucket is not a taxonomy.
    assert len(catalogue["domains"]) > 1


def test_an_empty_registry_yields_an_empty_catalogue():
    result = agent_catalogue({})
    assert result["agents"] == []
    assert result["totals"]["agents"] == 0
    assert result["domains"] == []
