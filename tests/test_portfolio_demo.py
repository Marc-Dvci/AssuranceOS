"""The risk-based planning demonstration, checked against canonical state.

The two rules the component rests on are checked by reading the scores back out
of the register rather than by asserting the code that produced them, and the
plan's exclusions are checked because a capacity-constrained plan that reports
only what it will do has hidden its most important decision.
"""

from __future__ import annotations

import pytest

from assuranceos.db.session import Database
from assuranceos.portfolio.demo import run_portfolio_demo


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "portfolio-demo.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def result(database) -> dict:
    return run_portfolio_demo(database=database)


def test_an_untested_control_leaves_the_risk_where_it_was(result):
    """The seeded data-store risk has a mature control nobody has tested.

    A register that let maturity alone reduce residual risk would show it green.
    """
    assert result["untested_control_reduces_nothing"]
    assert "asserted but untested" in result["untested_control_rationale"]


def test_a_risk_nobody_has_looked_at_ranks_above_its_residual(result):
    assert result["uncertainty_raises_priority"]


def test_minimum_coverage_forces_the_never_audited_critical_entities_in(result):
    forced = set(result["forced_by_minimum_coverage"])
    assert {"ast-r-data", "ast-r-iam"} <= forced


def test_the_plan_reports_what_it_declined_and_what_that_leaves_uncovered(result):
    assert result["excluded"]
    assert all(item["reason"] for item in result["excluded"])
    assert result["blind_spots"] == ["AST-R-VENDOR"]
    assert 0 < result["coverage_ratio"] < 1


def test_a_scenario_reports_that_mandatory_coverage_no_longer_fits(result):
    """Cutting capacity does not silently trim a required audit.

    The scenario returns an undeliverable plan and says so, which is the answer
    the budget conversation actually needs.
    """
    scenario = result["scenario_reduced_capacity"]
    assert scenario["deliverable"] is False
    assert any("needs a decision" in note for note in scenario["notes"])


def test_neither_the_rating_nor_the_plan_can_be_made_official_by_an_agent(result):
    assert "attributable to a person" in result["agent_approval_refused"]


def test_an_undeliverable_plan_cannot_be_approved(result):
    assert "cannot be approved as it stands" in result["undeliverable_plan_refused"]


def test_approving_the_plan_recorded_what_it_accepted(result):
    residual = result["accepted_residual"]
    assert residual["accepted_by"] == "dana.director@asteria.example"
    assert residual["excluded"]
    assert residual["blind_spots"]
    assert residual["uncovered_priority"] > 0


def test_the_lifecycle_is_reconstructable_from_audit_events(result):
    assert result["audit_event_types"] == [
        "plan.approved",
        "plan.proposed",
        "risk.assessed",
    ]
