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


class RiskAssessment(Base):
    """A scored, reproducible rating of one risk at one point in time.

    Assessments are versioned rather than overwritten. "What did we think this
    risk was last year, and on what basis" is the question an audit committee asks
    when a rating moves, and a mutable rating column cannot answer it.
    """

    __tablename__ = "risk_assessments"
    __table_args__ = (
        UniqueConstraint("risk_id", "version", name="uq_risk_assessment_version"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("residual >= 0 AND residual <= 1", name="residual_range"),
    )

    assessment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_id: Mapped[str] = mapped_column(
        ForeignKey("risks.risk_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    factors_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    components_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    inherent: Mapped[float] = mapped_column(Float, nullable=False)
    residual: Mapped[float] = mapped_column(Float, nullable=False)
    rating: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    audit_priority: Mapped[float] = mapped_column(Float, nullable=False)
    uncovered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The date the score was computed *as at*, which is not the same as when it
    #: was recorded. Ratings are recomputed retrospectively often enough that
    #: conflating the two makes staleness unmeasurable.
    as_at: Mapped[date] = mapped_column(Date, nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    #: A rating a person set aside, with their reason. The computed value is kept
    #: alongside it so the disagreement stays visible.
    official_rating: Mapped[str | None] = mapped_column(String(32))
    official_reason: Mapped[str | None] = mapped_column(Text)
    official_by: Mapped[str | None] = mapped_column(String(128))


class AssuranceCoverage(Base):
    """Assurance obtained over a risk from a named source on a named date."""

    __tablename__ = "assurance_coverage"

    coverage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_id: Mapped[str] = mapped_column(
        ForeignKey("risks.risk_id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("audit_universe_entities.entity_id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    obtained_on: Mapped[date] = mapped_column(Date, nullable=False)
    scope_note: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(255))
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="SET NULL")
    )
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PlanProposal(Base, TimestampMixin):
    """One recommended plan over one horizon, with what it excluded.

    The exclusions are stored with the proposal rather than derived later. An
    approval given without seeing what was left out is not an informed one, and a
    proposal that cannot show what it declined cannot evidence that it was.
    """

    __tablename__ = "plan_proposals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_plan_proposal_version"),
        CheckConstraint("planned_days >= 0", name="planned_days_non_negative"),
    )

    proposal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("audit_plans.plan_id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    scenario: Mapped[str] = mapped_column(String(64), nullable=False, default="baseline")
    horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    planned_json: Mapped[list[JsonObject]] = mapped_column(JSON, nullable=False, default=list)
    excluded_json: Mapped[list[JsonObject]] = mapped_column(JSON, nullable=False, default=list)
    blind_spots_json: Mapped[list[JsonObject]] = mapped_column(
        JSON, nullable=False, default=list
    )
    planned_days: Mapped[float] = mapped_column(Float, nullable=False)
    plannable_days: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    uncovered_priority: Mapped[float] = mapped_column(Float, nullable=False)
    notes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    proposed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    #: What approving this proposal accepted. Written at approval time from the
    #: exclusions on record, so the acceptance is attributable to the version of
    #: the plan that was actually in front of the approver.
    accepted_residual_json: Mapped[JsonObject] = mapped_column(
        JSON, nullable=False, default=dict
    )
