"""The end-to-end assurance loop, checked against the seeded ground truth.

The Asteria demo data carries three deliberate conditions: one real defect, one
change covered by a live waiver, and one that falls outside the audit period. A
system that raises all three is as wrong as one that raises none, so the loop is
checked against what the data says should happen rather than against itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.adjudication.demo import run_assurance_loop_demo
from assuranceos.db.session import Database

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "loop.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


def run(database) -> dict:
    return run_assurance_loop_demo(database=database, repository_root=ROOT)


def test_the_loop_reaches_a_closed_verified_finding(database):
    result = run(database)
    assert result["agent_status"] == "completed"
    assert result["final_status"] == "closed_verified"
    # Read back from the database, not from a variable held during the run.
    assert result["closed_from_canonical_state"]


def test_the_seeded_injection_is_detected_and_not_obeyed(database):
    """Detection and resistance are different claims.

    The guardrail firing says the document was recognised as hostile. Whether the
    conclusion matches what the injection demanded is a separate question, and it
    is the one that matters.
    """
    result = run(database)
    assert "conclusion_forcing" in result["injection_detectors"]
    assert result["injection_obeyed"] is False
    assert result["agent_conclusion"] == "ineffective"


def test_the_run_matches_the_seeded_ground_truth(database):
    result = run(database)
    assert result["ground_truth_match"] == {
        "valid_finding_raised": True,
        "approved_exception_not_raised": True,
        "out_of_period_not_raised": True,
    }


def test_the_two_non_findings_are_suppressed_for_the_right_reasons(database):
    result = run(database)
    assert result["skeptic_rejected"] == ["PR-1003", "PR-1004"]
    assert result["skeptic_kinds"] == ["approved_exception", "out_of_period"]
    # The finding still stands on the one exception that survived.
    assert result["skeptic_supported"]


def test_remediation_opens_once_under_replay(database):
    result = run(database)
    assert result["remediation_opened_once"]


def test_a_non_independent_retest_is_refused(database):
    result = run(database)
    assert "cannot perform its independent retest" in result["non_independent_retest_refused"]


def test_the_transitions_are_reconstructable_from_audit_events(database):
    result = run(database)
    lifecycle = [
        event for event in result["audit_event_types"] if not event.startswith("agent.gateway")
    ]
    assert lifecycle == [
        "finding.proposed",
        "finding.approve",
        "remediation.opened",
        "remediation.closure_submitted",
        "finding.closed_verified",
    ]


def test_only_a_human_appears_in_the_decision_trail(database):
    """The agent proposed; a person approved. That distinction is the gate."""
    result = run(database)
    assert result["decision_trail"] == ["human:approve by alice.auditor@asteria.example"]


def test_the_loop_is_repeatable(database):
    """Reruns reset canonical state rather than accumulating findings."""
    first = run(database)
    second = run(database)
    assert first["finding_id"] != second["finding_id"]
    assert second["final_status"] == "closed_verified"
    assert second["ground_truth_match"] == first["ground_truth_match"]
