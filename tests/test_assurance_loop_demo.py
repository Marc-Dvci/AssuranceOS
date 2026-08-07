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
        "finding.materiality_assessed",
        "finding.quality_review_passed",
        "finding.approve",
        "finding.disputed",
        "finding.dispute_upheld",
        "remediation.opened",
        "remediation.ticket_filed",
        # The second sync adopts the ticket the first one created rather than
        # filing another. A run in which both events read ``ticket_filed`` is a
        # duplicate-ticket regression.
        "remediation.ticket_reconciled",
        "remediation.closure_submitted",
        "finding.closed_verified",
    ]


def test_only_people_appear_in_the_decision_trail(database):
    """The agent proposed and scored; people approved and adjudicated.

    Both entries name a person. An agent reaching either of these decisions is the
    failure the component exists to make impossible.
    """
    result = run(database)
    assert result["decision_trail"] == [
        "human:approve by alice.auditor@asteria.example",
        "dispute:upheld by dana.director@asteria.example",
    ]


def test_approval_is_refused_until_materiality_and_quality_review_exist(database):
    result = run(database)
    refusal = result["premature_approval_refused"]
    assert "no materiality assessment exists" in refusal
    assert "no passing quality review exists" in refusal


def test_materiality_escalates_a_severity_the_agent_understated(database):
    """The agent proposed ``medium``; the policy computed a ``high`` floor.

    Nothing in the demo sets the severity. It is read back from canonical state
    after the assessment, so the escalation is the policy's and not the script's.
    """
    result = run(database)
    assert result["materiality_score"] == 2.0
    assert result["materiality_severity_floor"] == "high"
    assert result["severity_escalated_by_materiality"]


def test_the_quality_reviewer_cannot_also_approve(database):
    result = run(database)
    assert "cannot also approve it" in result["reviewer_cannot_approve"]


def test_an_open_dispute_blocks_remediation(database):
    result = run(database)
    assert result["dispute_status_while_open"] == "disputed"
    assert "cannot move to 'remediation_open'" in result["dispute_blocks_remediation"]
    assert result["dispute_resolution_status"] == "approved"


def test_the_jira_ticket_is_filed_once_even_when_local_state_forgets_it(database):
    """The second sync runs with no local reference and still files nothing new.

    That is the case the correlation lookup exists for: a crash between the
    provider's create and this side's commit. A local guard alone would open a
    second ticket here.
    """
    result = run(database)
    assert result["jira_ticket_filed_once"]
    assert result["jira_ticket_ref"] == "AUD-417"
    assert result["jira_correlation_key"].startswith("assuranceos:")


def test_the_loop_is_repeatable(database):
    """Reruns reset canonical state rather than accumulating findings."""
    first = run(database)
    second = run(database)
    assert first["finding_id"] != second["finding_id"]
    assert second["final_status"] == "closed_verified"
    assert second["ground_truth_match"] == first["ground_truth_match"]
