"""The governance demonstration must prove its claims from canonical state."""

from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.db import Database
from assuranceos.governance.demo import (
    GOVERNANCE_DEMO_ENGAGEMENT_ID,
    GOVERNANCE_DEMO_TENANT_ID,
    run_governance_demo,
)
from assuranceos.governance.persistence import GovernanceRecorder

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path: Path):
    db = Database.from_sqlite_path(tmp_path / "governance-demo.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


def test_governance_demo_proves_each_control(database: Database):
    result = run_governance_demo(database=database, repository_root=ROOT)

    assert result["tenant_id"] == GOVERNANCE_DEMO_TENANT_ID
    assert result["engagement_id"] == GOVERNANCE_DEMO_ENGAGEMENT_ID
    assert result["status"] == "completed"

    # The seeded injection is detected and neutralised, and the agent still reaches
    # the conclusion the evidence supports rather than the one the payload demanded.
    assert result["injection_neutralised"]
    assert {"conclusion_forcing", "credential_harvesting"} <= set(
        result["injection_detectors"]
    )
    assert result["conclusion"] == "ineffective"

    # One legitimate call allowed; four denials across four distinct mechanisms.
    assert result["allowed_tool_calls"] == ["evidence.capture"]
    assert result["gateway_allow_count"] == 1
    assert result["gateway_deny_count"] == 4
    assert any("absent from execution envelope" in d for d in result["runtime_denials"])
    assert any("guardrails" in d for d in result["runtime_denials"])
    assert result["replay_denial"].startswith("identity:")
    assert "not bound to this execution envelope" in result["replay_denial"]
    assert "revoked" in result["revocation_denial"]

    # Everything above is readable back out of the database.
    assert result["persisted_decisions"] == 5
    assert result["persisted_blocked_findings"] == ["path_traversal"]
    assert result["chain_rebuilt_from_database"]
    assert result["chain_spans"] > 0
    assert "assuranceos.agent.task" in result["chain_render"]


def test_governance_demo_is_deterministic_and_repeatable(database: Database):
    """A demonstration that cannot be re-run is a screenshot, not evidence."""
    first = run_governance_demo(database=database, repository_root=ROOT)
    second = run_governance_demo(database=database, repository_root=ROOT)

    for key in (
        "status",
        "conclusion",
        "injection_detectors",
        "allowed_tool_calls",
        "runtime_denials",
        "gateway_allow_count",
        "gateway_deny_count",
        "persisted_decisions",
        "persisted_blocked_findings",
        "chain_spans",
    ):
        assert first[key] == second[key], f"{key} differed between runs"

    # The reset is real: a repeat run does not accumulate rows.
    recorder = GovernanceRecorder(database)
    assert len(recorder.list_decisions(GOVERNANCE_DEMO_TENANT_ID)) == 5
