"""Materiality as a computed step rather than an asserted adjective.

Severity is a judgement an agent is happy to make and bad at defending. This
module removes the judgement from the model and leaves it the only part it can
actually contribute: whether a named qualitative factor is present, each of which
must cite evidence.

The scoring is deliberately plain arithmetic over declared inputs:

* a **quantitative** term from the exception rate against a policy threshold,
  suppressed when the population is too small for a rate to mean anything;
* a **monetary** term from the exposure against a policy threshold;
* a **qualitative** term from the weights of the factors that were asserted *and*
  evidenced.

The three combine by ``max``, not by sum. A finding that is quantitatively tiny
but regulator-reportable is material, and averaging the terms would dilute
exactly the factor that should dominate.

The resulting score maps to a severity *floor*. The floor may raise a proposed
severity automatically; lowering it below the floor is an override, which the
service records with an actor and a reason instead of performing silently.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

#: Ordered weakest to strongest so that comparisons are index comparisons.
SEVERITY_ORDER: tuple[str, ...] = ("low", "medium", "high", "critical")


def severity_rank(severity: str) -> int:
    """Position of a severity in :data:`SEVERITY_ORDER`.

    An unknown severity ranks at the top rather than the bottom: a value the
    policy does not recognise must not be treated as the mildest one.
    """
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return len(SEVERITY_ORDER) - 1


class QualitativeFactor(StrEnum):
    """The named ways a numerically small exception can still matter.

    A closed set rather than free text. Free-text factors cannot be weighted by a
    policy, cannot be compared across engagements, and cannot be audited for
    over-use by an agent that has learned they raise severity.
    """

    REGULATORY_REPORTABLE = "regulatory_reportable"
    FRAUD_INDICATOR = "fraud_indicator"
    REPEAT_FINDING = "repeat_finding"
    CUSTOMER_IMPACT = "customer_impact"
    FINANCIAL_STATEMENT_IMPACT = "financial_statement_impact"
    PERVASIVE_ACROSS_UNITS = "pervasive_across_units"
    CONTROL_ENVIRONMENT_WEAKNESS = "control_environment_weakness"
    MANAGEMENT_OVERRIDE = "management_override"


#: Default weights, expressed on the same scale as the quantitative term: a
#: factor at 1.0 is on its own sufficient to reach the materiality threshold.
DEFAULT_FACTOR_WEIGHTS: dict[QualitativeFactor, float] = {
    QualitativeFactor.REGULATORY_REPORTABLE: 2.0,
    QualitativeFactor.FRAUD_INDICATOR: 3.0,
    QualitativeFactor.MANAGEMENT_OVERRIDE: 3.0,
    QualitativeFactor.REPEAT_FINDING: 1.5,
    QualitativeFactor.FINANCIAL_STATEMENT_IMPACT: 2.0,
    QualitativeFactor.CUSTOMER_IMPACT: 1.0,
    QualitativeFactor.PERVASIVE_ACROSS_UNITS: 1.5,
    QualitativeFactor.CONTROL_ENVIRONMENT_WEAKNESS: 1.0,
}


class FactorAssertion(BaseModel):
    """A qualitative factor claimed to apply, with what supports it.

    ``evidence_ids`` is required and non-empty. This is the single control that
    stops materiality inflation: an agent that wants a higher severity has to
    point at a record, and a reviewer can follow the pointer.
    """

    model_config = {"extra": "forbid"}

    factor: QualitativeFactor
    rationale: str = Field(min_length=3, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class MaterialityPolicy(BaseModel):
    """The configured thresholds a materiality score is measured against.

    Held as data, versioned by ``policy_id``, and stored with every assessment so
    a score computed under an old policy stays interpretable after the policy
    changes.
    """

    model_config = {"extra": "forbid"}

    policy_id: str = Field(default="default-v1", min_length=1, max_length=64)
    #: Exception rate at which the quantitative term reaches the threshold.
    exception_rate_threshold: float = Field(default=0.05, gt=0, le=1)
    #: Below this population a rate is noise, so the quantitative term is dropped
    #: and the finding stands or falls on its qualitative factors.
    population_floor: int = Field(default=20, ge=0)
    monetary_threshold: float | None = Field(default=None, gt=0)
    factor_weights: dict[QualitativeFactor, float] = Field(
        default_factory=lambda: dict(DEFAULT_FACTOR_WEIGHTS)
    )
    #: Score at or above which each severity floor applies, strongest first.
    severity_bands: list[tuple[float, str]] = Field(
        default_factory=lambda: [(3.0, "critical"), (2.0, "high"), (1.0, "medium")]
    )

    @model_validator(mode="after")
    def bands_must_descend(self) -> "MaterialityPolicy":
        thresholds = [threshold for threshold, _ in self.severity_bands]
        if thresholds != sorted(thresholds, reverse=True):
            raise ValueError("severity_bands must be ordered from highest threshold down")
        unknown = {name for _, name in self.severity_bands} - set(SEVERITY_ORDER)
        if unknown:
            raise ValueError(f"severity_bands names unknown severities: {sorted(unknown)}")
        return self


class MaterialityInputs(BaseModel):
    """The measured facts a materiality score is computed from.

    The population and the exception count are counts from the deterministic test,
    not estimates. Requiring them here is what makes the score reproducible: two
    people with the same inputs and the same policy get the same number.
    """

    model_config = {"extra": "forbid"}

    population_size: int = Field(ge=0)
    exception_count: int = Field(ge=0)
    monetary_exposure: float | None = Field(default=None, ge=0)
    factors: list[FactorAssertion] = Field(default_factory=list)

    @model_validator(mode="after")
    def population_must_reconcile(self) -> "MaterialityInputs":
        """More exceptions than population is an arithmetic error, not a finding.

        Caught here rather than downstream because a population that does not
        reconcile invalidates every rate computed from it, and an audit that
        reports a 140% exception rate has already lost the argument.
        """
        if self.exception_count > self.population_size:
            raise ValueError(
                f"exception_count {self.exception_count} exceeds population_size "
                f"{self.population_size}; the population does not reconcile"
            )
        duplicates = sorted(
            {
                item.factor.value
                for item in self.factors
                if [other.factor for other in self.factors].count(item.factor) > 1
            }
        )
        if duplicates:
            raise ValueError(f"qualitative factor asserted twice: {', '.join(duplicates)}")
        return self


class MaterialityResult(BaseModel):
    """The computed score, its terms, and the severity floor it implies."""

    policy_id: str
    score: float
    material: bool
    severity_floor: Literal["low", "medium", "high", "critical"]
    components: dict[str, Any]
    rationale: str


def assess(inputs: MaterialityInputs, policy: MaterialityPolicy | None = None) -> MaterialityResult:
    """Score materiality from measured inputs under a policy.

    Pure and total: no database, no clock, no model. Everything that makes the
    result what it is appears in the arguments, which is what allows the stored
    assessment to be recomputed and compared.
    """
    policy = policy or MaterialityPolicy()

    if inputs.population_size >= policy.population_floor and inputs.population_size > 0:
        rate = inputs.exception_count / inputs.population_size
        quantitative = rate / policy.exception_rate_threshold
    else:
        rate = (
            inputs.exception_count / inputs.population_size if inputs.population_size else 0.0
        )
        quantitative = 0.0

    if policy.monetary_threshold and inputs.monetary_exposure is not None:
        monetary = inputs.monetary_exposure / policy.monetary_threshold
    else:
        monetary = 0.0

    qualitative = sum(
        policy.factor_weights.get(item.factor, 0.0) for item in inputs.factors
    )

    score = max(quantitative, monetary, qualitative)
    severity_floor = "low"
    for threshold, name in policy.severity_bands:
        if score >= threshold:
            severity_floor = name
            break

    dominant = max(
        (("quantitative", quantitative), ("monetary", monetary), ("qualitative", qualitative)),
        key=lambda item: item[1],
    )[0]
    if score == 0.0:
        dominant = "none"

    components = {
        "quantitative": round(quantitative, 6),
        "monetary": round(monetary, 6),
        "qualitative": round(qualitative, 6),
        "exception_rate": round(rate, 6),
        "population_below_floor": inputs.population_size < policy.population_floor,
        "dominant_term": dominant,
        "factors": [item.factor.value for item in inputs.factors],
    }

    if components["population_below_floor"]:
        basis = (
            f"population {inputs.population_size} is below the policy floor "
            f"{policy.population_floor}, so the exception rate is not scored"
        )
    else:
        basis = (
            f"{inputs.exception_count} of {inputs.population_size} "
            f"({rate:.1%}) against a {policy.exception_rate_threshold:.1%} threshold"
        )
    factor_text = (
        "; qualitative factors: " + ", ".join(item.factor.value for item in inputs.factors)
        if inputs.factors
        else "; no qualitative factors asserted"
    )
    rationale = (
        f"score {score:.2f} under policy {policy.policy_id} driven by the {dominant} term: "
        f"{basis}{factor_text}"
    )

    return MaterialityResult(
        policy_id=policy.policy_id,
        score=round(score, 6),
        material=score >= 1.0,
        severity_floor=severity_floor,  # type: ignore[arg-type]
        components=components,
        rationale=rationale,
    )


def content_hash(
    *,
    code: str,
    title: str,
    severity: str,
    criteria: str,
    observed_condition: str,
    risk_statement: str,
    evidence_ids: list[str],
    exception_keys: list[str],
) -> str:
    """A stable digest of the material content of a finding.

    Materiality assessments and quality reviews are bound to this digest. If the
    finding is edited afterwards — a different severity, different criteria,
    different evidence — the digest moves and the earlier review no longer
    applies, which is the behaviour a reviewer would expect and the one a status
    column alone cannot give.

    Deliberately excludes ``version``, timestamps and status: a review is about
    the substance, and re-versioning unchanged substance should not invalidate it.
    """
    payload = {
        "code": code,
        "title": title,
        "severity": severity,
        "criteria": criteria,
        "observed_condition": observed_condition,
        "risk_statement": risk_statement,
        "evidence_ids": sorted(evidence_ids),
        "exception_keys": sorted(exception_keys),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
