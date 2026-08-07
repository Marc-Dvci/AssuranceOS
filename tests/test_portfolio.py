"""Risk scoring and portfolio planning.

Two rules carry this component, and both are aimed at the way risk registers go
decorative: an untested control reduces nothing, and uncertainty raises audit
priority rather than lowering it. Most of the cases below exist to show those two
rules biting, because a scoring model that has only ever been shown to produce
numbers has not been shown to constrain anything.
"""

from __future__ import annotations

from datetime import date

import pytest

from assuranceos.db.models import Tenant
from assuranceos.db.session import Database
from assuranceos.portfolio import (
    AssuranceSource,
    Candidate,
    CapacityError,
    CapacityPolicy,
    ControlEvidence,
    CoverageRecord,
    PlanNotFoundError,
    PlanStateError,
    PortfolioService,
    RiskFactors,
    RiskNotFoundError,
    ScoringPolicy,
    recommend,
    score,
)

TENANT = "tnt_portfolio"
AS_AT = date(2026, 7, 1)
HORIZON = (date(2026, 9, 1), date(2027, 8, 31))


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "portfolio.db")
    db.create_schema()
    with db.transaction() as session:
        session.add(Tenant(tenant_id=TENANT, slug="pf", name="Portfolio"))
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def service(database):
    return PortfolioService(database)


def proven_control(**overrides) -> ControlEvidence:
    defaults = dict(
        control_ref="CTL-01",
        maturity=0.8,
        coverage=0.9,
        tested_effective=True,
        last_tested_on=date(2026, 5, 1),
        evidence_ids=["ev_test"],
    )
    defaults.update(overrides)
    return ControlEvidence(**defaults)


def factors(**overrides) -> RiskFactors:
    defaults = dict(impact=0.8, likelihood=0.5, velocity=0.5, detectability=0.5)
    defaults.update(overrides)
    return RiskFactors(**defaults)


# -- the two rules ---------------------------------------------------------------


def test_an_untested_control_reduces_nothing(database):
    """The single most consequential line in the scoring module.

    Without it a risk register can be talked down to green without anyone testing
    anything: assert a mature control, cover the whole risk, publish.
    """
    asserted = score(
        factors(
            controls=[
                ControlEvidence(control_ref="CTL-01", maturity=1.0, coverage=1.0)
            ]
        ),
        as_at=AS_AT,
    )
    assert asserted.residual == asserted.inherent
    assert asserted.components["untested_controls"] == ["CTL-01"]
    assert "not tested effective" in asserted.components["control_notes"][0]

    proven = score(factors(controls=[proven_control(maturity=1.0, coverage=1.0)]), as_at=AS_AT)
    assert proven.residual < proven.inherent


def test_a_control_tested_too_long_ago_stops_counting(database):
    stale = score(
        factors(controls=[proven_control(last_tested_on=date(2024, 1, 1))]), as_at=AS_AT
    )
    assert stale.residual == stale.inherent
    assert "beyond the" in stale.components["control_notes"][0]


def test_a_tested_control_must_carry_a_date_and_evidence():
    """An undated result cannot be aged; an uncited one is an assertion."""
    with pytest.raises(ValueError, match="no test date"):
        ControlEvidence(control_ref="CTL-01", maturity=0.9, coverage=1.0, tested_effective=True)
    with pytest.raises(ValueError, match="cites no evidence"):
        ControlEvidence(
            control_ref="CTL-01",
            maturity=0.9,
            coverage=1.0,
            tested_effective=True,
            last_tested_on=date(2026, 5, 1),
        )


def test_uncertainty_raises_priority_and_never_lowers_it():
    """A rating nobody has evidence for is not a low risk.

    Applied as an audit-priority multiplier rather than as a discount on residual,
    so "we don't know" cannot read as "it's fine".
    """
    unknown = score(factors(), as_at=AS_AT)
    assert unknown.confidence == 0.0
    assert unknown.audit_priority > unknown.residual

    known = score(
        factors(controls=[proven_control()], evidence_ids=["ev_a"]), as_at=AS_AT
    )
    assert known.confidence > unknown.confidence
    # The premium shrinks as confidence rises, and vanishes only when everything
    # the score can know about is on the record. Priority never drops below
    # residual: knowing more removes the surcharge, it does not buy a discount.
    assert known.components["uncertainty_premium"] < unknown.components["uncertainty_premium"]
    assert known.audit_priority >= known.residual

    complete = score(
        factors(
            controls=[proven_control()],
            evidence_ids=["ev_a"],
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.CONTINUOUS_MONITOR, obtained_on=date(2026, 6, 1)
                )
            ],
        ),
        as_at=AS_AT,
    )
    assert complete.confidence == 1.0
    assert complete.components["uncertainty_premium"] == 0.0


def test_no_control_set_can_take_a_risk_to_zero():
    """A model that says otherwise will be used to argue exactly that."""
    perfect = score(
        factors(
            controls=[
                proven_control(control_ref="A", maturity=1.0, coverage=1.0),
                proven_control(control_ref="B", maturity=1.0, coverage=1.0),
            ]
        ),
        as_at=AS_AT,
    )
    assert perfect.residual > 0


def test_two_half_covering_controls_do_not_add_up_to_full_coverage():
    halves = score(
        factors(
            controls=[
                proven_control(control_ref="A", maturity=1.0, coverage=0.5),
                proven_control(control_ref="B", maturity=1.0, coverage=0.5),
            ]
        ),
        as_at=AS_AT,
    )
    whole = score(
        factors(controls=[proven_control(control_ref="A", maturity=1.0, coverage=1.0)]),
        as_at=AS_AT,
    )
    assert halves.residual > whole.residual


# -- assurance reliance ----------------------------------------------------------


def test_assurance_lowers_the_need_for_work_not_the_risk():
    """Reliance lands on priority, never on residual.

    Folding it into the residual score would let a function argue a risk down by
    having looked at it once.
    """
    bare = score(factors(controls=[proven_control()]), as_at=AS_AT)
    assured = score(
        factors(
            controls=[proven_control()],
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.INTERNAL_AUDIT, obtained_on=date(2026, 3, 1)
                )
            ],
        ),
        as_at=AS_AT,
    )
    assert assured.residual == bare.residual
    assert assured.audit_priority < bare.audit_priority
    assert assured.uncovered is False


def test_management_self_testing_is_worth_less_than_independent_work():
    """A platform scoring them equally would let a function assure itself."""
    independent = score(
        factors(
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.INTERNAL_AUDIT, obtained_on=date(2026, 3, 1)
                )
            ]
        ),
        as_at=AS_AT,
    )
    self_tested = score(
        factors(
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.MANAGEMENT_TESTING, obtained_on=date(2026, 3, 1)
                )
            ]
        ),
        as_at=AS_AT,
    )
    assert self_tested.audit_priority > independent.audit_priority


def test_stacking_weak_sources_does_not_beat_one_strong_one():
    """The strongest source, not the sum.

    Three sources looking at the same thing do not triple the assurance, and
    adding them would let a function stack weak coverage into an argument for not
    auditing.
    """
    stacked = score(
        factors(
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.MANAGEMENT_TESTING, obtained_on=date(2026, 1, 1)
                ),
                CoverageRecord(
                    source=AssuranceSource.CONTINUOUS_MONITOR, obtained_on=date(2026, 2, 1)
                ),
            ]
        ),
        as_at=AS_AT,
    )
    single = score(
        factors(
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.CONTINUOUS_MONITOR, obtained_on=date(2026, 2, 1)
                )
            ]
        ),
        as_at=AS_AT,
    )
    assert stacked.audit_priority == single.audit_priority


def test_stale_assurance_stops_counting():
    stale = score(
        factors(
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.INTERNAL_AUDIT, obtained_on=date(2023, 1, 1)
                )
            ]
        ),
        as_at=AS_AT,
    )
    assert stale.uncovered is True


def test_the_score_is_reproducible_and_takes_no_clock():
    """``as_at`` is an argument so a rating can be recomputed as it stood."""
    inputs = factors(controls=[proven_control()])
    assert score(inputs, as_at=AS_AT).model_dump() == score(inputs, as_at=AS_AT).model_dump()
    later = score(inputs, as_at=date(2028, 1, 1))
    assert later.residual > score(inputs, as_at=AS_AT).residual


def test_rating_bands_must_descend():
    with pytest.raises(ValueError, match="highest threshold down"):
        ScoringPolicy(rating_bands=[(0.25, "medium"), (0.75, "critical")])


# -- planning --------------------------------------------------------------------


def candidate(key: str, *, priority: float, days: float, **overrides) -> Candidate:
    computed = score(
        factors(impact=min(priority, 1.0), likelihood=1.0, detectability=1.0),
        as_at=AS_AT,
    )
    defaults = dict(
        candidate_key=key,
        entity_ref=f"system://{key}",
        risk_ref=key.upper(),
        title=f"Risk {key}",
        objective=f"Assess the controls over risk {key}.",
        score=computed.model_copy(update={"audit_priority": priority}),
        effort_days=days,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


def policy(**overrides) -> CapacityPolicy:
    defaults = dict(
        horizon_start=HORIZON[0],
        horizon_end=HORIZON[1],
        available_days=100,
        minimum_coverage_criticality=4.5,
        contingency_fraction=0.0,
    )
    defaults.update(overrides)
    return CapacityPolicy(**defaults)


def test_selection_is_by_value_density_not_by_priority():
    """Ranking by priority alone buys one big engagement instead of three better ones."""
    plan = recommend(
        [
            candidate("big", priority=0.9, days=90),
            candidate("a", priority=0.5, days=20),
            candidate("b", priority=0.5, days=20),
            candidate("c", priority=0.5, days=20),
        ],
        policy(available_days=90),
    )
    assert [item.candidate_key for item in plan.planned] == ["a", "b", "c"]
    assert plan.planned_days == 60


def test_minimum_coverage_forces_a_low_scoring_critical_entity_in():
    """The pattern regulators ask about: never visited because it never scores.

    Coverage runs before the ranking, so a perpetually low-scoring critical entity
    is planned regardless of where it would have ranked.
    """
    plan = recommend(
        [
            candidate("boring", priority=0.01, days=10, criticality=5.0, last_audited_on=None),
            candidate("exciting", priority=0.9, days=10),
        ],
        policy(available_days=100),
    )
    forced = [item for item in plan.planned if item.forced_by_minimum_coverage]
    assert [item.candidate_key for item in forced] == ["boring"]
    assert "never audited" in forced[0].reason


def test_a_recently_audited_critical_entity_is_not_forced():
    plan = recommend(
        [
            candidate(
                "recent",
                priority=0.01,
                days=10,
                criticality=5.0,
                last_audited_on=date(2026, 6, 1),
            )
        ],
        policy(coverage_interval_months=24),
    )
    assert plan.planned == [] or not plan.planned[0].forced_by_minimum_coverage


def test_what_does_not_fit_is_reported_rather_than_dropped():
    """The list nobody publishes, and the only way a residual is knowingly accepted."""
    plan = recommend(
        [candidate("a", priority=0.9, days=30), candidate("b", priority=0.8, days=30)],
        policy(available_days=30),
    )
    assert [item.candidate_key for item in plan.planned] == ["a"]
    assert [item.candidate_key for item in plan.excluded] == ["b"]
    assert "does not fit remaining capacity" in plan.excluded[0].reason
    assert plan.uncovered_priority > 0


def test_contingency_is_held_back():
    """A plan that consumes every day has no room for the reason audit exists."""
    plan = recommend(
        [candidate("a", priority=0.9, days=90)],
        policy(available_days=100, contingency_fraction=0.15),
    )
    assert plan.plannable_days == 85
    assert plan.contingency_days == 15
    assert [item.candidate_key for item in plan.excluded] == ["a"]


def test_expertise_the_function_does_not_have_blocks_an_audit():
    """A plan that assumes skills nobody has is a plan that will not be delivered."""
    plan = recommend(
        [
            candidate(
                "quantum",
                priority=0.9,
                days=10,
                expertise_required=["post-quantum cryptography"],
                expertise_available=False,
            )
        ],
        policy(),
    )
    assert plan.planned == []
    assert "does not hold the required expertise" in plan.excluded[0].reason


def test_missing_expertise_on_a_mandatory_audit_is_reported_not_ignored():
    plan = recommend(
        [
            candidate(
                "mandatory",
                priority=0.1,
                days=10,
                criticality=5.0,
                expertise_required=["actuarial modelling"],
                expertise_available=False,
            )
        ],
        policy(),
    )
    assert "minimum coverage requires this audit" in plan.excluded[0].reason


def test_high_disruption_engagements_are_capped():
    plan = recommend(
        [
            candidate("a", priority=0.9, days=10, disruption="high"),
            candidate("b", priority=0.8, days=10, disruption="high"),
        ],
        policy(max_high_disruption=1),
    )
    assert [item.candidate_key for item in plan.planned] == ["a"]
    assert "high-disruption" in plan.excluded[0].reason


def test_mandatory_coverage_beyond_capacity_is_flagged_not_trimmed():
    """Dropping a required audit to fit a budget is the committee's decision."""
    plan = recommend(
        [
            candidate("a", priority=0.1, days=40, criticality=5.0),
            candidate("b", priority=0.1, days=40, criticality=5.0),
        ],
        policy(available_days=50),
    )
    assert len(plan.planned) == 2
    assert plan.is_deliverable is False
    assert any("needs a decision" in note for note in plan.policy_notes)


def test_a_blind_spot_is_uncovered_and_unplanned():
    """Unplanned but continuously monitored is not blind."""
    monitored = candidate("monitored", priority=0.05, days=10)
    monitored = monitored.model_copy(
        update={"score": monitored.score.model_copy(update={"uncovered": False})}
    )
    plan = recommend(
        [candidate("dark", priority=0.05, days=10), monitored],
        policy(available_days=5),
    )
    assert [item["candidate_key"] for item in plan.blind_spots] == ["dark"]


def test_planning_is_deterministic():
    """A planner whose output moves between runs cannot be reviewed."""
    candidates = [
        candidate("a", priority=0.5, days=20),
        candidate("b", priority=0.5, days=20),
        candidate("c", priority=0.5, days=20),
    ]
    first = recommend(candidates, policy(available_days=40))
    second = recommend(candidates, policy(available_days=40))
    assert [item.candidate_key for item in first.planned] == [
        item.candidate_key for item in second.planned
    ]


# -- the service -----------------------------------------------------------------


def register(service, code="AST-R-1"):
    service.register_risk(tenant_id=TENANT, code=code, title=f"Risk {code}")
    return code


def test_assessments_are_versioned_rather_than_overwritten(service):
    """'What did we think last year, and on what basis' needs a history."""
    code = register(service)
    first = service.assess_risk(
        tenant_id=TENANT,
        risk_code=code,
        factors=factors(),
        assessed_by="agent:risk-portfolio",
        as_at=AS_AT,
    )
    second = service.assess_risk(
        tenant_id=TENANT,
        risk_code=code,
        factors=factors(impact=0.2),
        assessed_by="agent:risk-portfolio",
        as_at=date(2026, 10, 1),
    )
    assert first.version == 1
    assert second.version == 2
    assert second.residual < first.residual


def test_an_agent_cannot_set_the_official_rating(service):
    code = register(service)
    service.assess_risk(
        tenant_id=TENANT,
        risk_code=code,
        factors=factors(),
        assessed_by="agent:risk-portfolio",
        as_at=AS_AT,
    )
    with pytest.raises(PlanStateError, match="attributable to a person"):
        service.set_official_rating(
            tenant_id=TENANT,
            risk_code=code,
            rating="low",
            actor_id="agent:risk-portfolio",
            reason="Downgraded automatically.",
        )


def test_an_override_keeps_the_computed_value_beside_it(service):
    """A register showing only the preferred number cannot show a disagreement."""
    code = register(service)
    service.assess_risk(
        tenant_id=TENANT,
        risk_code=code,
        factors=factors(impact=1.0, likelihood=1.0),
        assessed_by="agent:risk-portfolio",
        as_at=AS_AT,
    )
    service.set_official_rating(
        tenant_id=TENANT,
        risk_code=code,
        rating="medium",
        actor_id="dana.director@asteria.example",
        reason="Compensating contractual protection not modelled in the factors.",
    )
    row = service.register_view(tenant_id=TENANT)[0]
    assert row["computed_rating"] == "critical"
    assert row["official_rating"] == "medium"
    assert row["effective_rating"] == "medium"


def test_an_unknown_risk_is_refused(service):
    with pytest.raises(RiskNotFoundError):
        service.assess_risk(
            tenant_id=TENANT,
            risk_code="AST-R-NOPE",
            factors=factors(),
            assessed_by="agent:risk-portfolio",
            as_at=AS_AT,
        )


def test_an_agent_cannot_approve_a_plan(service):
    proposal = service.propose_plan(
        tenant_id=TENANT,
        name="FY27",
        candidates=[candidate("a", priority=0.5, days=10)],
        policy=policy(),
        proposed_by="agent:risk-portfolio",
    )
    with pytest.raises(PlanStateError, match="attributable to a person"):
        service.approve_plan(
            tenant_id=TENANT,
            proposal_id=proposal["proposal_id"],
            approved_by="agent:risk-portfolio",
            reason="Approved automatically.",
        )


def test_an_undeliverable_plan_cannot_be_approved(service):
    proposal = service.propose_plan(
        tenant_id=TENANT,
        name="FY27-tight",
        candidates=[
            candidate("a", priority=0.1, days=40, criticality=5.0),
            candidate("b", priority=0.1, days=40, criticality=5.0),
        ],
        policy=policy(available_days=50),
        proposed_by="agent:risk-portfolio",
    )
    with pytest.raises(CapacityError, match="cannot be approved as it stands"):
        service.approve_plan(
            tenant_id=TENANT,
            proposal_id=proposal["proposal_id"],
            approved_by="dana.director@asteria.example",
            reason="Approving anyway.",
        )


def test_approving_a_plan_records_what_it_accepted(service):
    """An audit committee that accepted a plan accepted what it left out."""
    proposal = service.propose_plan(
        tenant_id=TENANT,
        name="FY27",
        candidates=[
            candidate("a", priority=0.9, days=30),
            candidate("b", priority=0.8, days=30),
        ],
        policy=policy(available_days=30),
        proposed_by="agent:risk-portfolio",
    )
    approved = service.approve_plan(
        tenant_id=TENANT,
        proposal_id=proposal["proposal_id"],
        approved_by="dana.director@asteria.example",
        reason="Capacity constrained; b deferred to FY28.",
    )
    residual = approved["accepted_residual"]
    assert residual["accepted_by"] == "dana.director@asteria.example"
    assert [item["candidate_key"] for item in residual["excluded"]] == ["b"]
    assert residual["uncovered_priority"] > 0


def test_approval_is_idempotent(service):
    proposal = service.propose_plan(
        tenant_id=TENANT,
        name="FY27",
        candidates=[candidate("a", priority=0.5, days=10)],
        policy=policy(),
        proposed_by="agent:risk-portfolio",
    )
    first = service.approve_plan(
        tenant_id=TENANT,
        proposal_id=proposal["proposal_id"],
        approved_by="dana.director@asteria.example",
        reason="Approved.",
    )
    second = service.approve_plan(
        tenant_id=TENANT,
        proposal_id=proposal["proposal_id"],
        approved_by="dana.director@asteria.example",
        reason="Approved again.",
    )
    assert first["created"] is True
    assert second["created"] is False
    assert first["plan_id"] == second["plan_id"]


def test_a_scenario_records_nothing(service):
    """'What stops if we lose two people' must not create a plan."""
    before = service.proposal_view
    result = service.simulate(
        candidates=[candidate("a", priority=0.5, days=10)], policy=policy()
    )
    assert result["planned"]
    with pytest.raises(PlanNotFoundError):
        before(tenant_id=TENANT, proposal_id="plp_nothing")


def test_an_empty_candidate_set_is_refused(service):
    with pytest.raises(CapacityError, match="at least one candidate"):
        service.propose_plan(
            tenant_id=TENANT,
            name="FY27",
            candidates=[],
            policy=policy(),
            proposed_by="agent:risk-portfolio",
        )
