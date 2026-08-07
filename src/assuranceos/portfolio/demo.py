"""Risk-based planning over the seeded Asteria universe.

The demonstration this component owes is that the plan is *derived* and that its
derivation is arguable. So the run below scores six risks from declared inputs,
shows the two rules that keep the scores honest, produces a plan under a capacity
that cannot fit everything, and reports what was excluded — then shows the plan
being recomputed under a budget cut without recording anything, and finally being
approved by a person who thereby accepts the residual.

Two of the seeded risks are constructed to make the rules visible:

* ``AST-R-DATA`` has a mature control covering the whole risk that **nobody has
  tested**. Its residual stays at inherent. A register that let maturity alone
  reduce it would show green.
* ``AST-R-VENDOR`` has almost nothing on record. Its confidence is low, which
  *raises* its audit priority rather than lowering it: nobody has looked.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..db.models import Tenant
from ..db.repositories import AuditEventRepository, TenantRepository
from ..db.session import Database
from .exceptions import CapacityError, PlanStateError
from .planning import Candidate, CapacityPolicy
from .scoring import (
    AssuranceSource,
    ControlEvidence,
    CoverageRecord,
    RiskFactors,
    RiskScore,
)
from .service import PortfolioService

DEMO_TENANT = "tnt_asteria"
AS_AT = date(2026, 7, 1)
HORIZON = (date(2026, 9, 1), date(2027, 8, 31))

#: The seeded universe. Effort and criticality are declared, not inferred: a
#: planner that estimates its own costs produces recommendations nobody can argue
#: with.
SEEDED: list[dict[str, Any]] = [
    {
        "code": "AST-R-SCM",
        "title": "Unauthorised production change",
        "entity": "github://asteria/api",
        "entity_type": "system",
        "criticality": 4.5,
        "effort_days": 18,
        "pack": "software-change-management@2.0.0",
        "last_audited_on": date(2024, 6, 30),
        "factors": RiskFactors(
            impact=0.8,
            likelihood=0.6,
            velocity=0.8,
            change_intensity=0.7,
            detectability=0.4,
            external_exposure=0.5,
            controls=[
                ControlEvidence(
                    control_ref="SCM-01",
                    maturity=0.6,
                    coverage=0.8,
                    tested_effective=True,
                    last_tested_on=date(2026, 2, 1),
                    evidence_ids=["ev_scm_test_2026"],
                )
            ],
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.INTERNAL_AUDIT,
                    obtained_on=date(2024, 6, 30),
                    scope_note="Prior-year change-management engagement.",
                )
            ],
            evidence_ids=["ev_change_policy"],
        ),
    },
    {
        "code": "AST-R-IAM",
        "title": "Terminated worker retains access",
        "entity": "okta://asteria",
        "entity_type": "system",
        "criticality": 4.8,
        "effort_days": 14,
        "pack": "identity-access@1.0.0",
        "last_audited_on": None,
        "factors": RiskFactors(
            impact=0.9,
            likelihood=0.5,
            velocity=0.9,
            change_intensity=0.4,
            detectability=0.3,
            external_exposure=0.6,
            controls=[
                ControlEvidence(
                    control_ref="IAM-01",
                    maturity=0.5,
                    coverage=0.7,
                    tested_effective=True,
                    last_tested_on=date(2026, 5, 1),
                    evidence_ids=["ev_iam_test_2026"],
                )
            ],
            evidence_ids=["ev_access_policy"],
        ),
    },
    {
        # The untested-control case. Mature on paper, covers everything, never
        # tested. Residual stays at inherent.
        "code": "AST-R-DATA",
        "title": "Customer data exposed through a misconfigured store",
        "entity": "gcs://asteria-customer-data",
        "entity_type": "data_store",
        "criticality": 5.0,
        "effort_days": 22,
        "pack": None,
        "last_audited_on": None,
        "factors": RiskFactors(
            impact=1.0,
            likelihood=0.4,
            velocity=1.0,
            change_intensity=0.5,
            detectability=0.2,
            external_exposure=0.9,
            controls=[
                ControlEvidence(
                    control_ref="DATA-01",
                    maturity=0.9,
                    coverage=1.0,
                    tested_effective=False,
                )
            ],
            evidence_ids=[],
        ),
    },
    {
        # The uncertainty case. Almost nothing on record; confidence is low, which
        # raises priority rather than lowering it.
        "code": "AST-R-VENDOR",
        "title": "Critical vendor fails without a tested continuity path",
        "entity": "vendor://payments-processor",
        "entity_type": "vendor",
        "criticality": 4.2,
        "effort_days": 12,
        "pack": None,
        "last_audited_on": None,
        "factors": RiskFactors(
            impact=0.8,
            likelihood=0.3,
            velocity=0.7,
            change_intensity=0.2,
            detectability=0.5,
            external_exposure=0.4,
        ),
    },
    {
        "code": "AST-R-PAM",
        "title": "Standing privilege accumulates without justification",
        "entity": "gcp://asteria-prod",
        "entity_type": "platform",
        "criticality": 4.6,
        "effort_days": 16,
        "pack": "privileged-access@1.0.0",
        "last_audited_on": date(2025, 11, 30),
        "disruption": "high",
        "factors": RiskFactors(
            impact=0.9,
            likelihood=0.45,
            velocity=0.9,
            change_intensity=0.3,
            detectability=0.35,
            external_exposure=0.5,
            controls=[
                ControlEvidence(
                    control_ref="PAM-01",
                    maturity=0.4,
                    coverage=0.5,
                    tested_effective=True,
                    last_tested_on=date(2025, 11, 30),
                    evidence_ids=["ev_pam_walkthrough"],
                )
            ],
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.CONTINUOUS_MONITOR,
                    obtained_on=date(2026, 6, 1),
                    scope_note="Daily standing-privilege drift monitor.",
                )
            ],
            evidence_ids=["ev_pam_standard"],
        ),
    },
    {
        # Low priority and expensive. Should be excluded, and the exclusion is the
        # thing the demonstration wants to show.
        "code": "AST-R-EXPENSE",
        "title": "Employee expense claims outside policy",
        "entity": "finance://expenses",
        "entity_type": "process",
        "criticality": 2.0,
        "effort_days": 20,
        "pack": None,
        "last_audited_on": date(2026, 3, 31),
        "factors": RiskFactors(
            impact=0.3,
            likelihood=0.4,
            velocity=0.2,
            change_intensity=0.1,
            detectability=0.8,
            external_exposure=0.1,
            controls=[
                ControlEvidence(
                    control_ref="EXP-01",
                    maturity=0.8,
                    coverage=0.9,
                    tested_effective=True,
                    last_tested_on=date(2026, 3, 31),
                    evidence_ids=["ev_expense_test"],
                )
            ],
            coverage=[
                CoverageRecord(
                    source=AssuranceSource.MANAGEMENT_TESTING,
                    obtained_on=date(2026, 3, 31),
                    scope_note="Quarterly management self-testing.",
                )
            ],
            evidence_ids=["ev_expense_policy"],
        ),
    },
]


def run_portfolio_demo(*, database: Database) -> dict[str, Any]:
    """Score the universe, plan under capacity, and report what was left out."""
    service = PortfolioService(database)
    _reset_and_seed(database)

    scores: dict[str, RiskScore] = {}
    for item in SEEDED:
        service.register_entity(
            tenant_id=DEMO_TENANT,
            entity_type=item["entity_type"],
            name=item["entity"],
            external_ref=item["entity"],
            criticality=item["criticality"],
        )
        service.register_risk(
            tenant_id=DEMO_TENANT, code=item["code"], title=item["title"]
        )
        assessment = service.assess_risk(
            tenant_id=DEMO_TENANT,
            risk_code=item["code"],
            factors=item["factors"],
            assessed_by="agent:risk-portfolio",
            as_at=AS_AT,
        )
        scores[item["code"]] = RiskScore(
            policy_id=assessment.policy_id,
            inherent=assessment.inherent,
            residual=assessment.residual,
            rating=assessment.rating,  # type: ignore[arg-type]
            confidence=assessment.confidence,
            audit_priority=assessment.audit_priority,
            uncovered=assessment.uncovered,
            components=dict(assessment.components_json or {}),
            rationale=assessment.rationale,
        )

    candidates = [
        Candidate(
            candidate_key=item["code"].lower(),
            entity_ref=item["entity"],
            risk_ref=item["code"],
            title=item["title"],
            objective=f"Assess whether the controls over {item['title'].lower()} operate.",
            score=scores[item["code"]],
            effort_days=item["effort_days"],
            criticality=item["criticality"],
            disruption=item.get("disruption", "medium"),
            last_audited_on=item["last_audited_on"],
            audit_pack_ref=item["pack"],
        )
        for item in SEEDED
    ]

    # Capacity deliberately below the total, so the plan has to decline something
    # and the exclusions are visible rather than theoretical.
    policy = CapacityPolicy(
        horizon_start=HORIZON[0],
        horizon_end=HORIZON[1],
        available_days=70,
        minimum_coverage_criticality=4.5,
        coverage_interval_months=24,
        contingency_fraction=0.15,
        max_high_disruption=1,
    )
    proposal = service.propose_plan(
        tenant_id=DEMO_TENANT,
        name="Asteria FY27 audit plan",
        candidates=candidates,
        policy=policy,
        proposed_by="agent:risk-portfolio",
    )

    # A scenario, recorded nowhere: what stops if the team loses a third of its
    # capacity. The question a head of audit is asked in a budget conversation.
    reduced = service.simulate(
        candidates=candidates,
        policy=policy.model_copy(update={"available_days": 45}),
    )

    agent_approval = _refusal(
        lambda: service.approve_plan(
            tenant_id=DEMO_TENANT,
            proposal_id=proposal["proposal_id"],
            approved_by="agent:risk-portfolio",
            reason="Approved automatically by the planning agent.",
        ),
        PlanStateError,
    )
    undeliverable = _undeliverable_refusal(service, candidates, policy)

    approved = service.approve_plan(
        tenant_id=DEMO_TENANT,
        proposal_id=proposal["proposal_id"],
        approved_by="dana.director@asteria.example",
        reason=(
            "Coverage of the two never-audited critical entities is the priority for "
            "FY27; the expense process is covered by management testing this year."
        ),
    )

    with database.read_session() as session:
        events = AuditEventRepository(session).list(DEMO_TENANT)

    data_score = scores["AST-R-DATA"]
    vendor_score = scores["AST-R-VENDOR"]
    return {
        "tenant_id": DEMO_TENANT,
        "as_at": AS_AT.isoformat(),
        "register": service.register_view(tenant_id=DEMO_TENANT),
        # An untested control reduces nothing. Read off the score rather than
        # asserted: residual equals inherent for the risk whose only control has
        # never been tested.
        "untested_control_reduces_nothing": (
            data_score.residual == data_score.inherent
            and data_score.components["untested_controls"] == ["DATA-01"]
        ),
        "untested_control_rationale": data_score.rationale,
        # Low confidence raises priority above residual rather than lowering it.
        "uncertainty_raises_priority": (
            vendor_score.confidence == 0.0
            and vendor_score.audit_priority > vendor_score.residual
        ),
        "planned": [item["candidate_key"] for item in proposal["planned"]],
        "planned_days": proposal["planned_days"],
        "plannable_days": proposal["plannable_days"],
        "forced_by_minimum_coverage": [
            item["candidate_key"]
            for item in proposal["planned"]
            if item["forced_by_minimum_coverage"]
        ],
        "excluded": [
            {"candidate": item["candidate_key"], "reason": item["reason"]}
            for item in proposal["excluded"]
        ],
        "blind_spots": [item["risk_ref"] for item in proposal["blind_spots"]],
        "coverage_ratio": proposal["coverage_ratio"],
        "notes": proposal["policy_notes"],
        "scenario_reduced_capacity": {
            "available_days": 45,
            "planned": [item["candidate_key"] for item in reduced["planned"]],
            "excluded": [item["candidate_key"] for item in reduced["excluded"]],
            "planned_days": reduced["planned_days"],
            "plannable_days": reduced["plannable_days"],
            "coverage_ratio": reduced["coverage_ratio"],
            # The scenario's real answer. Mandatory coverage alone no longer fits,
            # so the plan is reported as undeliverable rather than trimmed to
            # match the budget: dropping a required audit is a decision for the
            # audit committee, not for a ranking rule.
            "deliverable": reduced["deliverable"],
            "notes": reduced["policy_notes"],
        },
        "agent_approval_refused": agent_approval,
        "undeliverable_plan_refused": undeliverable,
        "plan_id": approved["plan_id"],
        # Approving the plan accepted what it leaves uncovered, attributably.
        "accepted_residual": approved["accepted_residual"],
        "audit_event_types": sorted({event["event_type"] for event in events}),
    }


def _refusal(action: Any, expected: Any) -> str:
    try:
        action()
    except expected as exc:  # type: ignore[misc]
        return str(exc)
    return ""


def _undeliverable_refusal(
    service: PortfolioService, candidates: list[Candidate], policy: CapacityPolicy
) -> str:
    """Show that a plan whose mandatory coverage exceeds capacity cannot be approved.

    Minimum coverage is not silently trimmed to fit. Dropping a mandatory audit to
    balance a budget is a decision for the audit committee, so the planner reports
    the overrun and the approval refuses.
    """
    tight = policy.model_copy(
        update={"available_days": 20, "minimum_coverage_criticality": 4.0}
    )
    proposal = service.propose_plan(
        tenant_id=DEMO_TENANT,
        name="Asteria FY27 audit plan (constrained)",
        candidates=candidates,
        policy=tight,
        proposed_by="agent:risk-portfolio",
    )
    return _refusal(
        lambda: service.approve_plan(
            tenant_id=DEMO_TENANT,
            proposal_id=proposal["proposal_id"],
            approved_by="dana.director@asteria.example",
            reason="Approving the constrained plan.",
        ),
        CapacityError,
    )


def _reset_and_seed(database: Database) -> None:
    with database.transaction() as session:
        tenant = TenantRepository(session).get(DEMO_TENANT)
        if tenant is not None:
            session.delete(tenant)
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(
                tenant_id=DEMO_TENANT,
                slug="asteria",
                name="Asteria Systems DemoCo",
                status="active",
                region="europe-west1",
            )
        )
