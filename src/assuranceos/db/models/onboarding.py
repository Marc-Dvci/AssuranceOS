from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, utc_now
from .common import JsonObject, TimestampMixin


class OnboardingWorkflow(Base, TimestampMixin):
    __tablename__ = "onboarding_workflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_key", name="uq_onboarding_workflow_key"),
        CheckConstraint(
            "status IN ('researching', 'profile_review', 'ready', 'approved')",
            name="onboarding_status",
        ),
    )

    workflow_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="researching")
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_domain: Mapped[str | None] = mapped_column(String(255))
    headquarters_country: Mapped[str | None] = mapped_column(String(2))
    industry_hint: Mapped[str | None] = mapped_column(String(128))
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("organization_profiles.profile_id", ondelete="CASCADE"), nullable=False
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    remaining_unknowns_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    readiness_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicSourceSnapshot(Base, TimestampMixin):
    __tablename__ = "public_source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "content_sha256", "source_url", name="uq_public_snapshot_content"
        ),
        CheckConstraint(
            "source_quality IN ('official', 'authoritative', 'reputable')",
            name="public_source_quality",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("onboarding_workflows.workflow_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    source_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="RESTRICT"), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excerpt_locator: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class OrganizationFactDecision(Base):
    __tablename__ = "organization_fact_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('accept', 'correct', 'not_applicable')", name="organization_fact_decision"
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("onboarding_workflows.workflow_id", ondelete="CASCADE"), nullable=False
    )
    fact_id: Mapped[str] = mapped_column(
        ForeignKey("organization_facts.fact_id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correction_fact_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization_facts.fact_id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
