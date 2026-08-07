from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, utc_now
from .common import JsonObject, TimestampMixin


class Finding(Base, TimestampMixin):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("engagement_id", "code", "version"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    finding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    business_objective: Mapped[str | None] = mapped_column(Text)
    risk_statement: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[str] = mapped_column(Text, nullable=False)
    observed_condition: Mapped[str] = mapped_column(Text, nullable=False)
    cause: Mapped[str | None] = mapped_column(Text)
    consequence: Mapped[str | None] = mapped_column(Text)
    affected_population_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The evidence the conclusion rests on, and the contradictions the skeptic
    # found against it. The contradictions are retained even when the finding is
    # approved: "we considered this and it did not hold" is part of the record,
    # and discarding it would leave a later reviewer unable to tell a searched
    # finding from an unexamined one.
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    contradictions_json: Mapped[list[JsonObject]] = mapped_column(
        JSON, nullable=False, default=list
    )
    exception_keys_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # When the contradiction search ran. An empty contradiction list is ambiguous
    # on its own — it means either "searched and found nothing" or "never
    # searched" — and the quality gate has to be able to tell those apart.
    skeptic_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skeptic_rationale: Mapped[str | None] = mapped_column(Text)
    source_run_id: Mapped[str | None] = mapped_column(String(64))
    # The identity that proposed the finding. Retest independence is measured
    # against this, so it has to be canonical rather than inferred from logs.
    authored_by: Mapped[str | None] = mapped_column(String(128))


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), index=True
    )
    finding_id: Mapped[str | None] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="CASCADE"), index=True
    )
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ManagementResponse(Base):
    __tablename__ = "management_responses"
    __table_args__ = (UniqueConstraint("finding_id", "version"),)

    response_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    action_plan: Mapped[str | None] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    closure_evidence_ids_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )


class RemediationAction(Base, TimestampMixin):
    __tablename__ = "remediation_actions"

    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_plan: Mapped[str] = mapped_column(Text, nullable=False)
    escalation_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    closure_evidence_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    declared_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An action is opened at most once per finding. The idempotency key is kept
    # so a replay can be recognised as a replay rather than merely deduplicated,
    # and the external reference is recorded so a retry never files a second
    # ticket in Jira or ServiceNow.
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    external_system: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    #: The Jira project key or ServiceNow table this action files into. Held per
    #: action rather than per deployment because two findings in one engagement can
    #: legitimately belong to different queues.
    external_target: Mapped[str | None] = mapped_column(String(128))
    external_ref: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(Text)
    # ``pending`` until a ticket exists, ``synced`` once one does, ``failed`` when
    # the provider refused. Kept distinct from ``external_ref`` so a failure is
    # visible rather than indistinguishable from never having tried.
    external_sync_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    external_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_error: Mapped[str | None] = mapped_column(Text)
    completed_by: Mapped[str | None] = mapped_column(String(128))


class MaterialityAssessment(Base):
    """A scored, reproducible judgement of whether a finding matters.

    The inputs are stored alongside the score because materiality is the step an
    automated pipeline is most tempted to assert rather than compute. Keeping the
    population, the exception count, the monetary exposure, the asserted
    qualitative factors and the policy that weighted them means the number can be
    recomputed by a reviewer who does not trust it.

    ``content_hash`` binds the assessment to the exact finding text it was made
    against, so editing the finding afterwards invalidates the assessment instead
    of silently carrying it forward.
    """

    __tablename__ = "materiality_assessments"
    __table_args__ = (
        CheckConstraint("score >= 0", name="score_non_negative"),
        CheckConstraint("population_size >= 0", name="population_non_negative"),
        CheckConstraint("exception_count >= 0", name="exception_count_non_negative"),
    )

    assessment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    population_size: Mapped[int] = mapped_column(Integer, nullable=False)
    exception_count: Mapped[int] = mapped_column(Integer, nullable=False)
    monetary_exposure: Mapped[float | None] = mapped_column(Float)
    factors_json: Mapped[list[JsonObject]] = mapped_column(JSON, nullable=False, default=list)
    components_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    severity_floor: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    # A de-escalation below the computed floor is an override, not a result. It
    # carries its own actor and reason so the weaker severity is attributable.
    override_severity: Mapped[str | None] = mapped_column(String(32))
    override_reason: Mapped[str | None] = mapped_column(Text)
    override_by: Mapped[str | None] = mapped_column(String(128))


class QualityReview(Base):
    """The methodology gate, held separately from the approval gate.

    Approval asks "do we stand behind this conclusion". Quality review asks "was
    this work performed properly" — support cited, contradictions searched,
    population reconciled, limitations stated. They are different questions and,
    here, different people: the reviewer may not be the author, and the approver
    may not be the reviewer.
    """

    __tablename__ = "quality_reviews"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checks_json: Mapped[list[JsonObject]] = mapped_column(JSON, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class FindingDispute(Base):
    """Management's contest of a finding, and its resolution.

    Rounds are numbered rather than overwritten. An audit whose disagreement
    history is a single mutable field cannot show what was contested, on what
    grounds, or who conceded — which is precisely what an audit committee asks
    about a finding that changed.
    """

    __tablename__ = "finding_disputes"
    __table_args__ = (UniqueConstraint("finding_id", "round_no"),)

    dispute_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ground: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    raised_by: Mapped[str] = mapped_column(String(128), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # The status the finding held when the dispute opened. An upheld dispute
    # restores it, so the record has to carry it rather than guess.
    prior_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Retest(Base, TimestampMixin):
    __tablename__ = "retests"

    retest_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[str] = mapped_column(
        ForeignKey("remediation_actions.action_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    outcome: Mapped[str | None] = mapped_column(String(64))
    procedure_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    performed_by: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    # What the retester was checked against when independence was enforced. Kept
    # so the separation-of-duties claim can be re-verified from the record rather
    # than taken on trust.
    independence_basis_json: Mapped[JsonObject] = mapped_column(
        JSON, nullable=False, default=dict
    )
