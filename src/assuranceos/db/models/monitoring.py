from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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


class ContinuousMonitor(Base, TimestampMixin):
    __tablename__ = "continuous_monitors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "monitor_key", "version", name="uq_monitor_version"),
        CheckConstraint(
            "status IN ('draft', 'active', 'suspended', 'retired')", name="monitor_status"
        ),
    )

    monitor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitor_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    test_id: Mapped[str] = mapped_column(String(128), nullable=False)
    test_version: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_freshness_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    exception_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplication_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    alert_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    response_sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    independence_preserved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    configuration_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class MonitorRun(Base, TimestampMixin):
    __tablename__ = "monitor_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_monitor_run_idempotency"),
        CheckConstraint(
            "status IN ('succeeded', 'suspended', 'failed')", name="monitor_run_status"
        ),
    )

    monitor_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("continuous_monitors.monitor_id", ondelete="CASCADE"), nullable=False, index=True
    )
    control_test_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("control_test_runs.run_id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    conclusion: Mapped[str] = mapped_column(String(64), nullable=False)
    exception_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class MonitorAlert(Base, TimestampMixin):
    __tablename__ = "monitor_alerts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "monitor_id", "deduplication_key", name="uq_monitor_alert_dedup"
        ),
        CheckConstraint(
            "status IN ('review_pending', 'acknowledged', 'resolved')", name="monitor_alert_status"
        ),
    )

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("continuous_monitors.monitor_id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitor_run_id: Mapped[str] = mapped_column(
        ForeignKey("monitor_runs.monitor_run_id", ondelete="CASCADE"), nullable=False
    )
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    exception_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="review_pending")
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    review_case_ref: Mapped[str | None] = mapped_column(String(255))
    details_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
