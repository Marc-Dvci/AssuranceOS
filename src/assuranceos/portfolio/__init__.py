"""The audit universe, risk assessment, and portfolio planning.

The front of the loop. Everything downstream answers "did this control work"; this
component answers the question before it — which controls are worth asking about,
and what choosing them leaves uncovered.
"""

from .exceptions import (
    CapacityError,
    PlanNotFoundError,
    PlanStateError,
    PortfolioError,
    RiskNotFoundError,
)
from .planning import (
    Candidate,
    CapacityPolicy,
    ExcludedItem,
    PlannedItem,
    PlanRecommendation,
    recommend,
)
from .repository import PortfolioRepository
from .scoring import (
    DEFAULT_RATING_BANDS,
    DEFAULT_RELIANCE,
    AssuranceSource,
    ControlEvidence,
    CoverageRecord,
    RiskFactors,
    RiskScore,
    ScoringPolicy,
    assurance_reliance,
    confidence_in,
    control_reduction,
    score,
)
from .service import PortfolioService

__all__ = [
    "DEFAULT_RATING_BANDS",
    "DEFAULT_RELIANCE",
    "AssuranceSource",
    "Candidate",
    "CapacityError",
    "CapacityPolicy",
    "ControlEvidence",
    "CoverageRecord",
    "ExcludedItem",
    "PlanNotFoundError",
    "PlanRecommendation",
    "PlanStateError",
    "PlannedItem",
    "PortfolioError",
    "PortfolioRepository",
    "PortfolioService",
    "RiskFactors",
    "RiskNotFoundError",
    "RiskScore",
    "ScoringPolicy",
    "assurance_reliance",
    "confidence_in",
    "control_reduction",
    "recommend",
    "score",
]
