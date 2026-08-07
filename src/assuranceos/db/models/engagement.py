from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .common import JsonObject, TimestampMixin


class Engagement(Base, TimestampMixin):
    __tablename__ = "engagements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint("period_end >= period_start", name="period_order"),
    )

    engagement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagement_templates.template_id", ondelete="SET NULL"), index=True
    )
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    audit_pack_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    scope_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scope_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EngagementTask(Base, TimestampMixin):
    __tablename__ = "engagement_tasks"
    __table_args__ = (
        UniqueConstraint("engagement_id", "task_key"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_key: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    assigned_agent_role: Mapped[str | None] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    model_policy: Mapped[str | None] = mapped_column(String(128))
    tool_policy: Mapped[str | None] = mapped_column(String(128))
    human_gate: Mapped[str | None] = mapped_column(String(128))
    error_class: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str | None] = mapped_column(String(32))


class TaskAttempt(Base):
    """Immutable execution-attempt record separate from mutable task state."""

    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt_number"),
        CheckConstraint("attempt_no > 0", name="positive_attempt_number"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'retry_scheduled', 'failed', 'lease_expired', 'cancelled')",
            name="valid_attempt_status",
        ),
        Index("ix_task_attempts_engagement", "tenant_id", "engagement_id", "started_at"),
    )

    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("engagement_tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_class: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_refs_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)



class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_task_id"),)

    dependency_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("engagement_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        ForeignKey("engagement_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    condition_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


Index(
    "ix_tasks_claimable",
    EngagementTask.status,
    EngagementTask.available_at,
    EngagementTask.priority,
    EngagementTask.lease_expires_at,
)
