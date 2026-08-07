"""The reporting demonstration, checked against canonical state.

Six unsupportable reports are attempted and refused, each with its own code, and
then the same report is published with the defects resolved rather than removed.
The distinction matters: a component that silently drops the paragraph it cannot
support has produced a more dangerous document than one that refuses.
"""

from __future__ import annotations

import pytest

from assuranceos.db.session import Database
from assuranceos.reporting.demo import run_reporting_demo


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "reporting-demo.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def result(database) -> dict:
    return run_reporting_demo(database=database)


def test_every_defect_is_refused_with_its_own_code(result):
    assert result["distinct_refusal_codes"] == [
        "cross_engagement_reuse",
        "inadmissible_evidence",
        "out_of_period_evidence",
        "tainted_sole_support",
        "undisclosed_contradiction",
        "unsupported_material_claim",
    ]
    assert all(result["refusals"].values())


def test_a_conclusion_citing_nothing_is_refused(result):
    assert result["refusals"]["cites_nothing"] == ["unsupported_material_claim"]


def test_a_conclusion_resting_only_on_flagged_evidence_is_refused(result):
    assert "tainted_sole_support" in result["refusals"]["tainted_sole_support"]


def test_the_supportable_report_renders(result):
    assert result["supportable_report_has_no_issues"]
    assert result["material_claims"] == 3


def test_the_contradiction_was_disclosed_rather_than_deleted(result):
    """Resolving a defect by removing the inconvenient evidence is the failure mode."""
    assert result["contradiction_in_issued_report"]
    assert len(result["limitations_in_issued_report"]) == 3


def test_a_report_cannot_be_issued_by_an_agent(result):
    assert "attributable to a person" in result["agent_issuance_refused"]


def test_the_issued_report_verifies_and_a_tampered_one_does_not(result):
    assert result["issued"]
    assert result["verification"]["digest_matches"] is True
    # No signing key is configured in the demo, so the signature is not checked
    # rather than checked and failed. The two must not collapse.
    assert result["verification"]["signature_checked"] is False
    assert result["verification"]["signature_valid"] is None
    assert result["tampered_report_detected"] == "digest mismatch detected"


def test_the_claim_graph_answers_where_a_record_was_used(result):
    assert result["evidence_usage"]
    assert result["evidence_usage"][0]["relationship"] == "supports"


def test_the_lifecycle_is_reconstructable_from_audit_events(result):
    assert result["audit_event_types"] == ["report.prepared", "report.issued"]
