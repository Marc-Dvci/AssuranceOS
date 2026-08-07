from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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

from ..base import Base, utc_now
from .common import JsonObject, TimestampMixin


class ControlTestRelease(Base, TimestampMixin):
    __tablename__ = "control_test_releases"
    __table_args__ = (
        UniqueConstraint("test_id", "version", name="uq_control_test_release_version"),
        CheckConstraint(
            "engine IN ('python', 'sql')", name="control_test_release_engine"
        ),
        CheckConstraint(
            "release_status IN ('draft', 'released', 'retired')",
            name="control_test_release_status",
        ),
    )

    release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    test_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(255), nullable=False)
    package_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    output_schema_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    parameter_schema_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    dataset_contracts_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    reconciliation_policy_json: Mapped[JsonObject] = mapped_column(
        JSON, nullable=False, default=dict
    )
    sampling_policy_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    resource_limits_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    allowed_libraries_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    release_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(128))
    signature_key_id: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class ControlTestRun(Base, TimestampMixin):
    __tablename__ = "control_test_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_control_test_run_idempotency"),
        CheckConstraint("period_end >= period_start", name="control_test_run_period_order"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'blocked', 'failed', 'cancelled')",
            name="control_test_run_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagement_tasks.task_id", ondelete="SET NULL"), index=True
    )
    release_id: Mapped[str] = mapped_column(
        ForeignKey("control_test_releases.release_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    test_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    test_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    population_count: Mapped[int | None] = mapped_column(Integer)
    sampled_count: Mapped[int | None] = mapped_column(Integer)
    reconciled_count: Mapped[int | None] = mapped_column(Integer)
    exception_count: Mapped[int | None] = mapped_column(Integer)
    population_complete: Mapped[bool | None] = mapped_column(Boolean)
    conclusion: Mapped[str | None] = mapped_column(String(64))
    input_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    execution_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    result_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    execution_environment_json: Mapped[JsonObject] = mapped_column(
        JSON, nullable=False, default=dict
    )
    failure_class: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class ControlTestDatasetBinding(Base):
    __tablename__ = "control_test_dataset_bindings"
    __table_args__ = (
        UniqueConstraint("run_id", "dataset_name", name="uq_control_test_run_dataset"),
        CheckConstraint(
            "dataset_role IN ('population', 'reference', 'exceptions')",
            name="control_test_dataset_role",
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("control_test_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_role: Mapped[str] = mapped_column(String(32), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sampled_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class ControlTestException(Base):
    __tablename__ = "control_test_exceptions"
    __table_args__ = (
        UniqueConstraint("run_id", "exception_key", name="uq_control_test_run_exception"),
        CheckConstraint(
            "status IN ('open', 'approved_exception', 'false_positive', 'resolved')",
            name="control_test_exception_status",
        ),
    )

    exception_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("control_test_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    exception_key: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    classification: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    attributes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


Index(
    "ix_control_test_runs_tenant_engagement",
    ControlTestRun.tenant_id,
    ControlTestRun.engagement_id,
    ControlTestRun.created_at,
)
Index(
    "ix_control_test_runs_release_status",
    ControlTestRun.release_id,
    ControlTestRun.status,
    ControlTestRun.created_at,
)
