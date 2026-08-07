"""Risk scoring, and the two rules that keep it honest.

Risk ratings are the part of an audit function most vulnerable to becoming
decoration. Everything is amber, nothing is ever downgraded on evidence, and the
plan that falls out of the ratings is the plan somebody wanted anyway. Two
structural rules here are aimed squarely at that:

**An untested control provides no residual reduction.** Management asserting that
a control works is not evidence that it does. A control with no test result on
record leaves inherent risk where it is, however mature it is claimed to be. This
is the single most consequential line in the module: without it, a risk register
can be talked down to green without anyone testing anything.

**Uncertainty raises priority and never lowers it.** A rating held with low
confidence is not a low risk — it is a risk nobody has looked at. Confidence is
therefore applied as an *audit-priority* multiplier rather than as a discount on
the residual score, so "we don't know" cannot read as "it's fine".

Everything is pure arithmetic over declared inputs under a versioned policy, for
the same reason materiality is: a rating a reviewer cannot recompute is a rating
they have to take on trust.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Bands the residual score maps onto, strongest first.
DEFAULT_RATING_BANDS: list[tuple[float, str]] = [
    (0.75, "critical"),
    (0.50, "high"),
    (0.25, "medium"),
]


class AssuranceSource(StrEnum):
    """Where assurance over a risk comes from, and how much it is worth.

    Reliance differs by source and the differences are not opinions: an internal
    audit engagement was performed by people who report to the audit committee,
    management self-testing was not, and a continuous monitor covers a narrow
    question continuously rather than a broad one once.
    """

    INTERNAL_AUDIT = "internal_audit"
    EXTERNAL_AUDIT = "external_audit"
    CONTINUOUS_MONITOR = "continuous_monitor"
    MANAGEMENT_TESTING = "management_testing"
    REGULATORY_EXAMINATION = "regulatory_examination"
    NONE = "none"


#: How much each source reduces the need for fresh audit work. Management
#: self-testing is worth something and is worth clearly less than independent
#: work; a platform that scored them equally would let a function assure itself.
DEFAULT_RELIANCE: dict[AssuranceSource, float] = {
    AssuranceSource.INTERNAL_AUDIT: 0.80,
    AssuranceSource.EXTERNAL_AUDIT: 0.70,
    AssuranceSource.REGULATORY_EXAMINATION: 0.60,
    AssuranceSource.CONTINUOUS_MONITOR: 0.50,
    AssuranceSource.MANAGEMENT_TESTING: 0.25,
    AssuranceSource.NONE: 0.0,
}


class ControlEvidence(BaseModel):
    """One control's contribution to reducing a risk.

    ``last_tested_on`` and ``tested_effective`` are separate on purpose. A control
    that was tested and *failed* is not the same as one that was never tested, and
    neither of them reduces residual risk — but the first is a finding and the
    second is a gap in the audit plan.
    """

    model_config = ConfigDict(extra="forbid")

    control_ref: str = Field(min_length=1, max_length=64)
    #: 0 = ad hoc, 1 = optimised. The design claim.
    maturity: float = Field(ge=0, le=1)
    #: What share of the risk this control addresses if it works.
    coverage: float = Field(ge=0, le=1)
    tested_effective: bool = False
    last_tested_on: date | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def a_tested_control_must_say_when(self) -> "ControlEvidence":
        """``tested_effective`` without a date is an assertion wearing a result's clothes."""
        if self.tested_effective and self.last_tested_on is None:
            raise ValueError(
                f"control {self.control_ref!r} is marked tested effective but carries "
                "no test date; an undated result cannot be aged"
            )
        if self.tested_effective and not self.evidence_ids:
            raise ValueError(
                f"control {self.control_ref!r} is marked tested effective but cites no "
                "evidence; a result nobody can look up is an assertion"
            )
        return self


class CoverageRecord(BaseModel):
    """Assurance obtained over this risk from somewhere other than this plan."""

    model_config = ConfigDict(extra="forbid")

    source: AssuranceSource
    obtained_on: date
    scope_note: str = Field(default="", max_length=2000)
    reference: str | None = Field(default=None, max_length=255)


class RiskFactors(BaseModel):
    """The declared inputs a residual rating is computed from."""

    model_config = ConfigDict(extra="forbid")

    impact: float = Field(ge=0, le=1)
    likelihood: float = Field(ge=0, le=1)
    #: How fast the risk materialises once it starts. A slow risk leaves time to
    #: detect and respond; a fast one does not.
    velocity: float = Field(default=0.5, ge=0, le=1)
    #: How much the underlying environment changed in the period. Change is where
    #: controls that used to work stop working.
    change_intensity: float = Field(default=0.0, ge=0, le=1)
    #: How likely a failure is to be noticed without the audit function looking.
    detectability: float = Field(default=0.5, ge=0, le=1)
    #: Regulatory, contractual, or public exposure if the risk materialises.
    external_exposure: float = Field(default=0.0, ge=0, le=1)
    controls: list[ControlEvidence] = Field(default_factory=list)
    coverage: list[CoverageRecord] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ScoringPolicy(BaseModel):
    """Configured weights and thresholds for risk scoring.

    Held as data and versioned by ``policy_id``, and stored with every assessment,
    so a rating computed under an old policy stays interpretable after the policy
    changes.
    """

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(default="default-v1", min_length=1, max_length=64)
    #: How much a fully mature, fully covering, *tested* control set can reduce
    #: inherent risk. Never 1.0: no control set reduces a risk to zero, and a
    #: model that says otherwise will eventually be used to argue exactly that.
    max_control_reduction: float = Field(default=0.75, gt=0, lt=1)
    #: Test results older than this stop counting as current.
    test_staleness_days: int = Field(default=365, ge=1)
    velocity_weight: float = Field(default=0.15, ge=0, le=1)
    change_weight: float = Field(default=0.20, ge=0, le=1)
    exposure_weight: float = Field(default=0.15, ge=0, le=1)
    reliance: dict[AssuranceSource, float] = Field(
        default_factory=lambda: dict(DEFAULT_RELIANCE)
    )
    coverage_staleness_days: int = Field(default=548, ge=1)
    rating_bands: list[tuple[float, str]] = Field(
        default_factory=lambda: list(DEFAULT_RATING_BANDS)
    )
    #: Below this, a rating counts as uncertain and its audit priority is raised.
    confidence_floor: float = Field(default=0.5, ge=0, le=1)
    #: How much low confidence raises audit priority at zero confidence.
    uncertainty_premium: float = Field(default=0.5, ge=0, le=2)

    @model_validator(mode="after")
    def bands_must_descend(self) -> "ScoringPolicy":
        thresholds = [threshold for threshold, _ in self.rating_bands]
        if thresholds != sorted(thresholds, reverse=True):
            raise ValueError("rating_bands must be ordered from highest threshold down")
        return self


class RiskScore(BaseModel):
    """A computed rating, its terms, and the confidence it is held with."""

    policy_id: str
    inherent: float
    residual: float
    rating: Literal["low", "medium", "high", "critical"]
    confidence: float
    #: Residual, adjusted for how stale the assurance over it is and for how
    #: little is known. This is what the planner ranks on; the residual score is
    #: what a risk register reports.
    audit_priority: float
    uncovered: bool
    components: dict[str, Any]
    rationale: str


def _rating_for(score: float, policy: ScoringPolicy) -> str:
    for threshold, name in policy.rating_bands:
        if score >= threshold:
            return name
    return "low"


def _days_between(later: date, earlier: date) -> int:
    return (later - earlier).days


def control_reduction(
    factors: RiskFactors, policy: ScoringPolicy, *, as_at: date
) -> tuple[float, list[str]]:
    """How much the control set may reduce inherent risk, and why.

    Only controls that were **tested effective**, and tested recently enough to be
    current, contribute anything. A control that is mature on paper, covers the
    whole risk, and has never been tested contributes zero — which is the rule
    that stops a risk register being talked down without evidence.

    Contributions combine multiplicatively over the residual gap rather than by
    addition, so two controls each covering half the risk do not sum to complete
    coverage.
    """
    notes: list[str] = []
    remaining = 1.0
    for control in factors.controls:
        if not control.tested_effective:
            notes.append(
                f"{control.control_ref}: not tested effective, contributes no reduction"
            )
            continue
        assert control.last_tested_on is not None  # enforced by the model validator
        age = _days_between(as_at, control.last_tested_on)
        if age > policy.test_staleness_days:
            notes.append(
                f"{control.control_ref}: last tested {age} days ago, beyond the "
                f"{policy.test_staleness_days}-day currency window"
            )
            continue
        contribution = control.maturity * control.coverage
        remaining *= 1.0 - contribution
        notes.append(
            f"{control.control_ref}: tested effective {age} days ago, "
            f"maturity {control.maturity:.2f} x coverage {control.coverage:.2f}"
        )
    reduction = (1.0 - remaining) * policy.max_control_reduction
    return reduction, notes


def assurance_reliance(
    factors: RiskFactors, policy: ScoringPolicy, *, as_at: date
) -> tuple[float, str | None]:
    """The strongest current assurance over this risk, and where it came from.

    The strongest rather than the sum: three sources looking at the same thing do
    not triple the assurance, and adding them would let a function stack weak
    coverage into an argument for not auditing.
    """
    best = 0.0
    best_source: str | None = None
    for record in factors.coverage:
        age = _days_between(as_at, record.obtained_on)
        if age > policy.coverage_staleness_days:
            continue
        value = policy.reliance.get(record.source, 0.0)
        if value > best:
            best = value
            best_source = record.source.value
    return best, best_source


def confidence_in(factors: RiskFactors, *, as_at: date) -> float:
    """How much is actually known about this risk.

    Built from what is on the record rather than stated by the assessor: whether
    any control was tested, whether the rating cites evidence, and whether any
    assurance is current. An assessor-supplied confidence would be the same
    unverifiable assertion the module exists to remove.
    """
    signals = 0.0
    tested = [item for item in factors.controls if item.tested_effective]
    if tested:
        signals += 0.4
    if factors.evidence_ids:
        signals += 0.2
    if any(item.evidence_ids for item in factors.controls):
        signals += 0.2
    if factors.coverage:
        recent = min(
            (_days_between(as_at, item.obtained_on) for item in factors.coverage),
            default=10_000,
        )
        if recent <= 365:
            signals += 0.2
    return round(min(signals, 1.0), 4)


def score(
    factors: RiskFactors, policy: ScoringPolicy | None = None, *, as_at: date
) -> RiskScore:
    """Compute inherent risk, residual risk, and audit priority.

    Pure and total. ``as_at`` is passed rather than read from a clock so a rating
    can be recomputed exactly as it stood on a past date.
    """
    policy = policy or ScoringPolicy()

    inherent = factors.impact * factors.likelihood
    # Aggravators raise inherent risk without being able to exceed 1. Applied to
    # the headroom above the current value, so a risk already near the ceiling
    # cannot be pushed past it by adding factors.
    aggravation = (
        policy.velocity_weight * factors.velocity
        + policy.change_weight * factors.change_intensity
        + policy.exposure_weight * factors.external_exposure
        # Poor detectability is an aggravator, so the term is inverted.
        + policy.velocity_weight * (1.0 - factors.detectability)
    )
    inherent = min(1.0, inherent + (1.0 - inherent) * min(aggravation, 1.0))

    reduction, control_notes = control_reduction(factors, policy, as_at=as_at)
    residual = inherent * (1.0 - reduction)

    reliance, reliance_source = assurance_reliance(factors, policy, as_at=as_at)
    confidence = confidence_in(factors, as_at=as_at)

    # Priority, not residual, is where reliance and confidence land. Existing
    # assurance lowers the need for *fresh audit work*; it does not lower the
    # risk. And low confidence raises priority: a risk nobody has looked at is
    # not a low risk.
    uncertainty = policy.uncertainty_premium * (1.0 - confidence)
    audit_priority = residual * (1.0 - reliance) * (1.0 + uncertainty)

    components = {
        "impact": factors.impact,
        "likelihood": factors.likelihood,
        "aggravation": round(min(aggravation, 1.0), 6),
        "control_reduction": round(reduction, 6),
        "assurance_reliance": round(reliance, 6),
        "assurance_source": reliance_source,
        "uncertainty_premium": round(uncertainty, 6),
        "tested_controls": [
            item.control_ref for item in factors.controls if item.tested_effective
        ],
        "untested_controls": [
            item.control_ref for item in factors.controls if not item.tested_effective
        ],
        "control_notes": control_notes,
    }

    untested = components["untested_controls"]
    rationale_parts = [
        f"inherent {inherent:.2f} from impact {factors.impact:.2f} x likelihood "
        f"{factors.likelihood:.2f} aggravated by {min(aggravation, 1.0):.2f}",
        (
            f"controls reduce it by {reduction:.2f}"
            if reduction
            else "no tested control reduces it"
        ),
    ]
    if untested:
        rationale_parts.append(
            f"{len(untested)} control(s) are asserted but untested and contribute nothing: "
            + ", ".join(sorted(untested))
        )
    if reliance_source:
        rationale_parts.append(
            f"current {reliance_source} assurance lowers the need for fresh work by "
            f"{reliance:.0%}"
        )
    else:
        rationale_parts.append("no current assurance from any source")
    if confidence < policy.confidence_floor:
        rationale_parts.append(
            f"confidence {confidence:.2f} is below the {policy.confidence_floor:.2f} floor, "
            f"raising audit priority by {uncertainty:.0%}"
        )

    return RiskScore(
        policy_id=policy.policy_id,
        inherent=round(inherent, 6),
        residual=round(residual, 6),
        rating=_rating_for(residual, policy),  # type: ignore[arg-type]
        confidence=confidence,
        audit_priority=round(audit_priority, 6),
        uncovered=reliance_source is None,
        components=components,
        rationale="; ".join(rationale_parts),
    )
