"""Types for finding adjudication, remediation, and independent retest.

The lifecycle is a state machine rather than a status column that anyone may set.
Each transition names the actor that may perform it and the evidence it requires,
because the point of the component is that a conclusion cannot become a closed
finding without passing gates a model cannot open on its own.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .materiality import MaterialityInputs, MaterialityPolicy


class FindingStatus(StrEnum):
    """Where a finding sits in its lifecycle.

    ``PROPOSED`` is the only state an agent can create. Everything past
    ``APPROVED`` requires a recorded human decision.
    """

    PROPOSED = "proposed"
    REJECTED = "rejected"
    APPROVED = "approved"
    DISPUTED = "disputed"
    WITHDRAWN = "withdrawn"
    REMEDIATION_OPEN = "remediation_open"
    REMEDIATION_DECLARED_COMPLETE = "remediation_declared_complete"
    RETEST_IN_PROGRESS = "retest_in_progress"
    CLOSED_VERIFIED = "closed_verified"
    REOPENED = "reopened"
    RISK_ACCEPTED = "risk_accepted"
    DEFERRED = "deferred"


class HumanDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    RETURN_FOR_REWORK = "return_for_rework"
    DEFER = "defer"
    ACCEPT_RISK = "accept_risk"


class DisputeGround(StrEnum):
    """The grounds on which management may contest a finding.

    A closed set, for the same reason the contradiction kinds are: "we disagree"
    is not reviewable, whereas "the criteria cite a superseded policy version" is.
    """

    CRITERIA_INCORRECT = "criteria_incorrect"
    CONDITION_INACCURATE = "condition_inaccurate"
    SEVERITY_OVERSTATED = "severity_overstated"
    MATERIALITY_DISPUTED = "materiality_disputed"
    EVIDENCE_SUPERSEDED = "evidence_superseded"
    COMPENSATING_CONTROL_OMITTED = "compensating_control_omitted"
    OUT_OF_SCOPE = "out_of_scope"


class DisputeResolution(StrEnum):
    """How a dispute ends.

    ``MODIFIED`` is the consequential one: accepting that the finding needs to
    change voids the quality review and the approval it was granted under, because
    both were given for text that no longer stands.
    """

    UPHELD = "upheld"
    MODIFIED = "modified"
    WITHDRAWN = "withdrawn"


class RetestOutcome(StrEnum):
    CLOSED_VERIFIED = "closed_verified"
    PARTIALLY_REMEDIATED = "partially_remediated"
    INEFFECTIVE = "ineffective"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REOPEN = "reopen"


#: Outcomes that close a finding. Every other outcome reopens it. Stating the
#: closing set positively means a new outcome added later defaults to reopening,
#: which is the safe direction for an audit conclusion.
CLOSING_OUTCOMES = frozenset({RetestOutcome.CLOSED_VERIFIED})


#: The permitted transitions. A transition absent from this table cannot happen,
#: which is what makes the lifecycle reviewable without reading the service.
ALLOWED_TRANSITIONS: dict[FindingStatus, frozenset[FindingStatus]] = {
    FindingStatus.PROPOSED: frozenset(
        {
            FindingStatus.APPROVED,
            FindingStatus.REJECTED,
            FindingStatus.DEFERRED,
            FindingStatus.RISK_ACCEPTED,
            FindingStatus.DISPUTED,
        }
    ),
    # A finding can be disputed after approval as well as before it. That is the
    # common case in practice: management sees the approved draft and contests it.
    FindingStatus.APPROVED: frozenset(
        {FindingStatus.REMEDIATION_OPEN, FindingStatus.DISPUTED}
    ),
    FindingStatus.DISPUTED: frozenset(
        {
            FindingStatus.PROPOSED,
            FindingStatus.APPROVED,
            FindingStatus.REJECTED,
            FindingStatus.WITHDRAWN,
        }
    ),
    FindingStatus.WITHDRAWN: frozenset(),
    FindingStatus.REMEDIATION_OPEN: frozenset(
        {FindingStatus.REMEDIATION_DECLARED_COMPLETE}
    ),
    FindingStatus.REMEDIATION_DECLARED_COMPLETE: frozenset(
        {FindingStatus.RETEST_IN_PROGRESS}
    ),
    FindingStatus.RETEST_IN_PROGRESS: frozenset(
        {FindingStatus.CLOSED_VERIFIED, FindingStatus.REOPENED}
    ),
    # A reopened finding rejoins the loop at remediation. It never returns to
    # `proposed`: the finding was already adjudicated and that history stands.
    FindingStatus.REOPENED: frozenset({FindingStatus.REMEDIATION_OPEN}),
    FindingStatus.CLOSED_VERIFIED: frozenset(),
    FindingStatus.REJECTED: frozenset(),
    FindingStatus.RISK_ACCEPTED: frozenset(),
    FindingStatus.DEFERRED: frozenset({FindingStatus.APPROVED, FindingStatus.REJECTED}),
}


class ContradictionKind(StrEnum):
    """Why a proposed finding may not be supportable.

    These are the standard ways a deterministic exception turns out not to be a
    finding. Naming them lets the skeptic produce an attributable reason rather
    than a bare rejection.
    """

    APPROVED_EXCEPTION = "approved_exception"
    OUT_OF_PERIOD = "out_of_period"
    COMPENSATING_CONTROL = "compensating_control"
    SUPERSEDED_EVIDENCE = "superseded_evidence"
    DUPLICATE_OF_EXISTING = "duplicate_of_existing"


class Contradiction(BaseModel):
    """One reason the proposed finding may not stand, with its evidence."""

    kind: ContradictionKind
    subject_ref: str
    detail: str = Field(min_length=3, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)


class ProposedFinding(BaseModel):
    """A finding an agent proposes from an accepted control-test exception.

    ``confidence`` is the agent's, and it is never sufficient on its own: the
    human gate is a separate record, not a threshold on this number.
    """

    model_config = {"extra": "forbid"}

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,63}$")
    title: str = Field(min_length=3, max_length=255)
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    criteria: str = Field(min_length=3)
    observed_condition: str = Field(min_length=3)
    risk_statement: str = Field(min_length=3)
    cause: str | None = None
    consequence: str | None = None
    business_objective: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    exception_keys: list[str] = Field(default_factory=list)
    affected_population: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    source_run_id: str | None = None

    @model_validator(mode="after")
    def must_cite_evidence(self) -> "ProposedFinding":
        """A finding with no evidence is an opinion.

        Rejecting this at the type boundary means no downstream state can hold an
        uncited finding, rather than each caller remembering to check.
        """
        if not self.evidence_ids:
            raise ValueError("a proposed finding must cite at least one evidence id")
        return self


class AdjudicationRequest(BaseModel):
    """A human decision on a proposed finding.

    ``actor_id`` is a person. The service refuses decisions attributed to an
    agent role, because an approval an agent can produce is not a human gate.
    """

    model_config = {"extra": "forbid"}

    finding_id: str
    decision: HumanDecision
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=3, max_length=4000)
    idempotency_key: str = Field(min_length=3, max_length=255)


class MaterialityRequest(BaseModel):
    """Ask for a materiality score over a finding.

    The inputs and the policy are supplied rather than inferred so that the same
    request replayed produces the same assessment. ``assessed_by`` may be an agent:
    scoring is a computation, and the thing an agent must not do is *approve*.
    """

    model_config = {"extra": "forbid"}

    finding_id: str
    inputs: MaterialityInputs
    policy: MaterialityPolicy | None = None
    assessed_by: str = Field(min_length=1, max_length=128)


class SeverityOverrideRequest(BaseModel):
    """Set a severity below the computed materiality floor.

    Deliberately its own request type. Lowering a severity is the move most worth
    attributing, and folding it into the assessment would let it happen as a side
    effect of rescoring.
    """

    model_config = {"extra": "forbid"}

    finding_id: str
    severity: Literal["low", "medium", "high", "critical"]
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=10, max_length=4000)


class QualityReviewRequest(BaseModel):
    """Submit a finding for the methodology gate."""

    model_config = {"extra": "forbid"}

    finding_id: str
    reviewer_id: str = Field(min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)


class DisputeRequest(BaseModel):
    """Management contests a finding on a stated ground."""

    model_config = {"extra": "forbid"}

    finding_id: str
    ground: DisputeGround
    statement: str = Field(min_length=10, max_length=4000)
    raised_by: str = Field(min_length=1, max_length=128)
    evidence_ids: list[str] = Field(default_factory=list)


class DisputeResolutionRequest(BaseModel):
    """The audit side's answer to a dispute."""

    model_config = {"extra": "forbid"}

    dispute_id: str
    resolution: DisputeResolution
    reason: str = Field(min_length=10, max_length=4000)
    resolved_by: str = Field(min_length=1, max_length=128)


class RemediationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    finding_id: str
    owner_ref: str = Field(min_length=1, max_length=255)
    due_date: date
    action_plan: str = Field(min_length=3, max_length=4000)
    idempotency_key: str = Field(min_length=3, max_length=255)
    closure_evidence_required: bool = True
    escalation_policy: dict[str, Any] = Field(default_factory=dict)
    external_system: Literal["none", "jira", "servicenow"] = "none"
    #: The Jira project key or ServiceNow table the ticket is filed in. Required
    #: by every provider and meaningless without one, so it is carried on the
    #: request rather than configured globally: two findings can belong to
    #: different queues.
    external_target: str | None = Field(default=None, max_length=128)


class ClosureSubmission(BaseModel):
    """Management's assertion that the action is done, plus its evidence."""

    model_config = {"extra": "forbid"}

    action_id: str
    response_text: str = Field(min_length=3, max_length=4000)
    submitted_by: str = Field(min_length=1, max_length=128)
    closure_evidence_ids: list[str] = Field(default_factory=list)
    action_plan: str | None = None


class RetestRequest(BaseModel):
    """An independent retest of a completed remediation.

    ``performed_by`` must differ from the identity that authored the finding and
    from the one that performed the remediation. The service enforces it; see
    :class:`~assuranceos.adjudication.exceptions.IndependenceError`.
    """

    model_config = {"extra": "forbid"}

    action_id: str
    procedure_ref: str = Field(min_length=1, max_length=255)
    performed_by: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=3, max_length=255)
    outcome: RetestOutcome
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str = Field(default="", max_length=4000)
    fresh_evidence_collected_at: datetime | None = None


class SkepticVerdict(BaseModel):
    """The result of searching for reasons a proposed finding should not stand."""

    supported: bool
    contradictions: list[Contradiction] = Field(default_factory=list)
    rationale: str = ""

    @property
    def rejection_reason(self) -> str:
        if self.supported:
            return ""
        kinds = ", ".join(sorted({c.kind.value for c in self.contradictions}))
        return f"contradicted by {kinds}: {self.rationale}" if kinds else self.rationale


class FindingView(BaseModel):
    """A finding and everything that has happened to it."""

    finding_id: str
    tenant_id: str
    engagement_id: str
    code: str
    version: int
    title: str
    status: FindingStatus
    severity: str
    confidence: float
    requires_human_approval: bool
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    retests: list[dict[str, Any]] = Field(default_factory=list)
    #: The digest of the finding's material content. Materiality assessments and
    #: quality reviews are bound to it, so exposing it lets a caller see at a
    #: glance whether the gates on record still apply to the text on record.
    content_hash: str = ""
    materiality: dict[str, Any] | None = None
    quality_reviews: list[dict[str, Any]] = Field(default_factory=list)
    disputes: list[dict[str, Any]] = Field(default_factory=list)
    #: Whether the finding currently satisfies every precondition for approval.
    #: Computed rather than stored: a stored flag would go stale the moment the
    #: finding was edited.
    approval_ready: bool = False
    approval_blockers: list[str] = Field(default_factory=list)


class RecurrenceMatch(BaseModel):
    """The same control failing again in a different engagement."""

    code: str
    engagement_ids: list[str]
    occurrences: int
    latest_status: FindingStatus
