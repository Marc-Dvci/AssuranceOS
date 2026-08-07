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
    external_ref: Mapped[str | None] = mapped_column(String(255))
    completed_by: Mapped[str | None] = mapped_column(String(128))


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
