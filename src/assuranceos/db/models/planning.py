from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
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


class AuditPlan(Base, TimestampMixin):
    __tablename__ = "audit_plans"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    horizon_start: Mapped[date | None] = mapped_column(Date)
    horizon_end: Mapped[date | None] = mapped_column(Date)
    coverage_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))


class EngagementTemplate(Base, TimestampMixin):
    __tablename__ = "engagement_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    audit_pack_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    objectives_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scope_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    preflight_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    workflow_definition_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )


class AuditSchedule(Base, TimestampMixin):
    __tablename__ = "audit_schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until >= effective_from",
            name="effective_window",
        ),
        CheckConstraint("catch_up_limit > 0", name="positive_catch_up_limit"),
        CheckConstraint(
            "max_concurrent_engagements > 0", name="positive_concurrency_limit"
        ),
    )

    schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("audit_plans.plan_id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("engagement_templates.template_id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    recurrence_rule: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_period_rule_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    business_calendar_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    blackout_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    preflight_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    launch_mode: Mapped[str] = mapped_column(
        String(64), nullable=False, default="approval_required"
    )
    missed_occurrence_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="launch_latest"
    )
    catch_up_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    overlap_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="prevent")
    max_concurrent_engagements: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_by: Mapped[str | None] = mapped_column(String(128))
    disable_reason: Mapped[str | None] = mapped_column(Text)


class ScheduleCursor(Base):
    __tablename__ = "schedule_cursors"

    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("audit_schedules.schedule_id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ScheduleOccurrence(Base):
    __tablename__ = "schedule_occurrences"
    __table_args__ = (
        UniqueConstraint("schedule_id", "nominal_due"),
        CheckConstraint("period_end >= period_start", name="period_order"),
    )

    occurrence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("audit_schedules.schedule_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="SET NULL"), unique=True
    )
    nominal_due: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="due")
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decision_by: Mapped[str | None] = mapped_column(String(128))
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_snapshot_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    template_snapshot_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    preflight_result_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
