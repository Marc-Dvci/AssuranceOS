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
from assuranceos.product import tenant_cockpit, trace_detail

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


def test_the_recorded_chain_is_discoverable_from_the_trace_list(database: Database):
    """Spans without a trace header are invisible to every list view.

    ``trace_detail`` tolerates a missing header, so a chain could be persisted,
    rebuilt, and still never reach the operator: the cockpit lists traces from
    the header table, and an empty list offers no trace id to look one up with.
    """
    result = run_governance_demo(database=database, repository_root=ROOT)

    cockpit = tenant_cockpit(database, GOVERNANCE_DEMO_TENANT_ID)
    traces = cockpit["governance"]["traces"]
    assert [item["trace_id"] for item in traces] == [result["trace_id"]]
    assert traces[0]["engagement_id"] == GOVERNANCE_DEMO_ENGAGEMENT_ID
    assert traces[0]["attributes"]["span_count"] == result["chain_spans"]

    # And the id the list hands over resolves to the chain behind it.
    detail = trace_detail(database, GOVERNANCE_DEMO_TENANT_ID, result["trace_id"])
    assert detail is not None
    assert len(detail["spans"]) == result["chain_spans"]


def test_a_repeated_run_updates_the_trace_header_rather_than_duplicating_it(
    database: Database,
):
    run_governance_demo(database=database, repository_root=ROOT)
    second = run_governance_demo(database=database, repository_root=ROOT, reset=False)

    traces = tenant_cockpit(database, GOVERNANCE_DEMO_TENANT_ID)["governance"]["traces"]
    assert second["trace_id"] in {item["trace_id"] for item in traces}
    assert len(traces) == len({item["trace_id"] for item in traces})


def test_an_opened_trace_carries_the_injection_detection(database: Database):
    """The detection has to survive into the projection an operator reads.

    Which detectors fired is recorded as a span *event*, not as a span attribute
    and not as a gateway decision — the neutralisation happens while inspecting
    inbound context, before any tool is called. A trace projection that drops
    events therefore shows an untroubled chain of allowed spans over a document
    that was in fact rewritten.
    """
    result = run_governance_demo(database=database, repository_root=ROOT)
    detail = trace_detail(database, GOVERNANCE_DEMO_TENANT_ID, result["trace_id"])

    detectors = {
        detector
        for span in detail["spans"]
        for event in span["events"]
        if event["name"] == "armor.neutralised"
        for detector in event["attributes"]["detectors"].split(",")
    }
    assert set(result["injection_detectors"]) == detectors

    # And the tool-call denial reaches the same view through the gateway record.
    assert "path_traversal" in {
        item["detector"] for item in detail["guardrail_findings"]
    }


def test_a_contained_attack_is_not_reported_as_a_failed_trace(database: Database):
    """Denied tool calls are the control working, not the run failing."""
    result = run_governance_demo(database=database, repository_root=ROOT)
    assert result["gateway_deny_count"] > 0

    traces = tenant_cockpit(database, GOVERNANCE_DEMO_TENANT_ID)["governance"]["traces"]
    header = next(item for item in traces if item["trace_id"] == result["trace_id"])
    assert header["status"] == "completed"
    assert header["attributes"]["denied_span_count"] > 0
