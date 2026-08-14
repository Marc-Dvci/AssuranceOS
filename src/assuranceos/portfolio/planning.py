"""Turning risk ratings into a defensible audit plan.

The output of this module is not "the plan". It is a *recommendation* plus the
part every audit plan leaves out and few state: what was not planned, and what
risk that leaves uncovered. A plan that lists only what it will do has hidden its
most important decision.

Selection is ordered by **value density** — priority per day of effort — rather
than by priority alone. Ranking by priority alone systematically buys one large
engagement instead of three smaller ones worth more in total, which is how a
capacity-constrained function ends up covering less than it could.

Two constraints sit in front of the ranking:

* **Minimum coverage.** Entities above a criticality threshold that have not been
  audited within the rolling horizon are forced into the plan before anything is
  ranked. Otherwise a perpetually low-scoring but critical entity is never
  visited, which is exactly the pattern regulators ask about.
* **Capacity.** Days are finite. What does not fit is reported as excluded, with
  the residual risk it leaves, rather than silently dropped.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .scoring import RiskScore
from ..text import counted


class Candidate(BaseModel):
    """One auditable thing, scored and costed.

    ``effort_days`` and the qualitative columns are declared rather than inferred.
    A planner that estimates its own costs is a planner whose recommendations
    cannot be argued with.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_key: str = Field(min_length=1, max_length=128)
    entity_ref: str = Field(min_length=1, max_length=128)
    risk_ref: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=255)
    objective: str = Field(min_length=10)
    score: RiskScore
    effort_days: float = Field(gt=0)
    criticality: float = Field(default=0, ge=0, le=5)
    disruption: Literal["low", "medium", "high"] = "medium"
    expertise_required: list[str] = Field(default_factory=list)
    #: Whether the audit function currently holds that expertise. A plan that
    #: assumes skills nobody has is a plan that will not be delivered.
    expertise_available: bool = True
    last_audited_on: date | None = None
    data_dependencies: list[str] = Field(default_factory=list)
    suggested_cadence_months: int = Field(default=12, ge=1, le=60)
    audit_pack_ref: str | None = None

    @property
    def value_density(self) -> float:
        """Audit priority per day of effort."""
        return self.score.audit_priority / self.effort_days


class CapacityPolicy(BaseModel):
    """What the audit function can actually deliver, and what it must cover."""

    model_config = ConfigDict(extra="forbid")

    horizon_start: date
    horizon_end: date
    available_days: float = Field(gt=0)
    #: Entities at or above this criticality must be covered within the horizon.
    minimum_coverage_criticality: float = Field(default=4.0, ge=0, le=5)
    #: How long since an audit before minimum coverage forces a revisit.
    coverage_interval_months: int = Field(default=24, ge=1, le=120)
    #: Fraction of capacity held back for unplanned and investigative work. A plan
    #: that consumes every available day has no room for the reason audit
    #: functions exist to be available.
    contingency_fraction: float = Field(default=0.15, ge=0, le=0.5)
    max_high_disruption: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def horizon_must_be_ordered(self) -> "CapacityPolicy":
        if self.horizon_end <= self.horizon_start:
            raise ValueError("horizon_end must be after horizon_start")
        return self

    @property
    def plannable_days(self) -> float:
        return self.available_days * (1.0 - self.contingency_fraction)


class PlannedItem(BaseModel):
    """A candidate the plan recommends, and why it is in."""

    candidate_key: str
    entity_ref: str
    risk_ref: str
    title: str
    objective: str
    effort_days: float
    audit_priority: float
    value_density: float
    rating: str
    confidence: float
    cadence_months: int
    reason: str
    forced_by_minimum_coverage: bool = False
    audit_pack_ref: str | None = None


class ExcludedItem(BaseModel):
    """A candidate the plan does not cover, and what that leaves exposed.

    The list nobody publishes. It is the honest half of a capacity-constrained
    plan and the only way an audit committee can accept the residual knowingly.
    """

    candidate_key: str
    entity_ref: str
    risk_ref: str
    title: str
    effort_days: float
    audit_priority: float
    rating: str
    reason: str
    uncovered: bool


class PlanRecommendation(BaseModel):
    """A recommended plan, its exclusions, and where the blind spots are."""

    horizon_start: date
    horizon_end: date
    planned: list[PlannedItem]
    excluded: list[ExcludedItem]
    blind_spots: list[dict[str, Any]]
    planned_days: float
    plannable_days: float
    contingency_days: float
    coverage_ratio: float
    uncovered_priority: float
    policy_notes: list[str]

    @property
    def is_deliverable(self) -> bool:
        return self.planned_days <= self.plannable_days


def _needs_coverage(candidate: Candidate, policy: CapacityPolicy) -> bool:
    """Whether minimum coverage forces this candidate into the plan."""
    if candidate.criticality < policy.minimum_coverage_criticality:
        return False
    if candidate.last_audited_on is None:
        return True
    due = candidate.last_audited_on + timedelta(
        days=int(policy.coverage_interval_months * 30.44)
    )
    return due <= policy.horizon_end


def recommend(
    candidates: list[Candidate], policy: CapacityPolicy
) -> PlanRecommendation:
    """Select a plan under capacity and minimum-coverage constraints.

    Deterministic: candidates are ordered by value density with a stable tiebreak
    on key, so two runs over the same inputs produce the same plan. A planner
    whose output moves between runs cannot be reviewed.
    """
    notes: list[str] = []
    forced = [item for item in candidates if _needs_coverage(item, policy)]
    optional = [item for item in candidates if item not in forced]

    planned: list[PlannedItem] = []
    excluded: list[ExcludedItem] = []
    spent = 0.0

    def admit(candidate: Candidate, *, reason: str, forced_flag: bool) -> None:
        nonlocal spent
        spent += candidate.effort_days
        planned.append(
            PlannedItem(
                candidate_key=candidate.candidate_key,
                entity_ref=candidate.entity_ref,
                risk_ref=candidate.risk_ref,
                title=candidate.title,
                objective=candidate.objective,
                effort_days=candidate.effort_days,
                audit_priority=candidate.score.audit_priority,
                value_density=round(candidate.value_density, 6),
                rating=candidate.score.rating,
                confidence=candidate.score.confidence,
                cadence_months=candidate.suggested_cadence_months,
                reason=reason,
                forced_by_minimum_coverage=forced_flag,
                audit_pack_ref=candidate.audit_pack_ref,
            )
        )

    def refuse(candidate: Candidate, *, reason: str) -> None:
        excluded.append(
            ExcludedItem(
                candidate_key=candidate.candidate_key,
                entity_ref=candidate.entity_ref,
                risk_ref=candidate.risk_ref,
                title=candidate.title,
                effort_days=candidate.effort_days,
                audit_priority=candidate.score.audit_priority,
                rating=candidate.score.rating,
                reason=reason,
                uncovered=candidate.score.uncovered,
            )
        )

    # Minimum coverage first. These are in regardless of how they rank, which is
    # the whole point: a critical entity that scores low every year still has to
    # be visited.
    for candidate in sorted(forced, key=lambda item: (-item.criticality, item.candidate_key)):
        if not candidate.expertise_available:
            refuse(
                candidate,
                reason=(
                    "minimum coverage requires this audit, but the function does not hold "
                    f"the required expertise ({', '.join(candidate.expertise_required)}); "
                    "it needs sourcing before it can be planned"
                ),
            )
            continue
        admit(
            candidate,
            reason=(
                f"minimum coverage: criticality {candidate.criticality:.1f} and "
                + (
                    "never audited"
                    if candidate.last_audited_on is None
                    else f"last audited {candidate.last_audited_on.isoformat()}"
                )
            ),
            forced_flag=True,
        )

    if spent > policy.plannable_days:
        # Reported rather than resolved. Dropping a mandatory audit to fit the
        # budget is a decision for the audit committee, not for a ranking rule.
        notes.append(
            f"minimum coverage alone requires {spent:.1f} days against "
            f"{policy.plannable_days:.1f} plannable; capacity or scope needs a decision"
        )

    high_disruption = sum(
        1
        for item in planned
        for candidate in candidates
        if candidate.candidate_key == item.candidate_key and candidate.disruption == "high"
    )

    for candidate in sorted(
        optional, key=lambda item: (-item.value_density, item.candidate_key)
    ):
        if not candidate.expertise_available:
            refuse(
                candidate,
                reason=(
                    "the function does not hold the required expertise: "
                    + ", ".join(candidate.expertise_required)
                ),
            )
            continue
        if candidate.disruption == "high" and high_disruption >= policy.max_high_disruption:
            refuse(
                candidate,
                reason=(
                    f"the plan already carries {high_disruption} high-disruption "
                    f"engagement(s), the configured maximum"
                ),
            )
            continue
        if spent + candidate.effort_days > policy.plannable_days:
            refuse(
                candidate,
                reason=(
                    f"does not fit remaining capacity "
                    f"({policy.plannable_days - spent:.1f} of "
                    f"{policy.plannable_days:.1f} days left)"
                ),
            )
            continue
        if candidate.disruption == "high":
            high_disruption += 1
        admit(
            candidate,
            reason=(
                f"value density {candidate.value_density:.4f} "
                f"(priority {candidate.score.audit_priority:.3f} over "
                f"{candidate.effort_days:.0f} days)"
            ),
            forced_flag=False,
        )

    # A blind spot is a risk with no current assurance from anywhere *and* no
    # place in this plan. A risk that is merely unplanned but continuously
    # monitored is not blind.
    planned_keys = {item.candidate_key for item in planned}
    blind_spots = [
        {
            "candidate_key": candidate.candidate_key,
            "entity_ref": candidate.entity_ref,
            "risk_ref": candidate.risk_ref,
            "rating": candidate.score.rating,
            "audit_priority": candidate.score.audit_priority,
            "confidence": candidate.score.confidence,
            "why": (
                "no current assurance from any source and not covered by this plan"
            ),
        }
        for candidate in candidates
        if candidate.score.uncovered and candidate.candidate_key not in planned_keys
    ]

    total_priority = sum(item.score.audit_priority for item in candidates)
    planned_priority = sum(item.audit_priority for item in planned)
    coverage_ratio = planned_priority / total_priority if total_priority else 1.0

    if blind_spots:
        notes.append(
            f"{counted(len(blind_spots), 'risk')} have no assurance from any source and no place "
            "in this plan"
        )
    if not notes:
        notes.append("every constraint was satisfied within the plannable capacity")

    return PlanRecommendation(
        horizon_start=policy.horizon_start,
        horizon_end=policy.horizon_end,
        planned=planned,
        excluded=excluded,
        blind_spots=blind_spots,
        planned_days=round(spent, 4),
        plannable_days=round(policy.plannable_days, 4),
        contingency_days=round(policy.available_days - policy.plannable_days, 4),
        coverage_ratio=round(coverage_ratio, 6),
        uncovered_priority=round(total_priority - planned_priority, 6),
        policy_notes=notes,
    )
