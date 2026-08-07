from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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


class ConnectorInstance(Base, TimestampMixin):
    """Tenant-owned connector configuration without credential material."""

    __tablename__ = "connector_instances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_key", name="uq_connector_instance_key"),
        CheckConstraint(
            "status IN ('draft', 'active', 'disabled', 'error')",
            name="valid_connector_status",
        ),
    )

    connector_instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    credential_ref: Mapped[str | None] = mapped_column(String(512))
    config_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    last_health_status: Mapped[str | None] = mapped_column(String(32))
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_details_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class CollectionGrant(Base, TimestampMixin):
    """Purpose-bound, time-bound authorization for a connector collection operation."""

    __tablename__ = "collection_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "grant_key", name="uq_collection_grant_key"),
        CheckConstraint(
            "status IN ('draft', 'active', 'revoked', 'expired')",
            name="valid_collection_grant_status",
        ),
        CheckConstraint("read_only", name="collection_grant_read_only"),
        Index("ix_collection_grant_active", "tenant_id", "connector_instance_id", "status"),
    )

    grant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.connector_instance_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_streams_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    resource_selectors_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)


class ConnectorCheckpoint(Base):
    __tablename__ = "connector_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "connector_instance_id", "stream", name="uq_connector_checkpoint"
        ),
        CheckConstraint("version > 0", name="positive_checkpoint_version"),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.connector_instance_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ConnectorRun(Base):
    __tablename__ = "connector_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_connector_run_idempotency"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="valid_connector_run_status",
        ),
        CheckConstraint("objects_seen >= 0", name="nonnegative_objects_seen"),
        CheckConstraint("objects_ingested >= 0", name="nonnegative_objects_ingested"),
        CheckConstraint("objects_unchanged >= 0", name="nonnegative_objects_unchanged"),
        Index("ix_connector_run_recent", "tenant_id", "connector_instance_id", "started_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.connector_instance_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grant_id: Mapped[str] = mapped_column(
        ForeignKey("collection_grants.grant_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_before_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    checkpoint_after_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    request_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    objects_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    objects_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    objects_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_fingerprint: Mapped[str | None] = mapped_column(String(64))
    schema_drift: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CollectedSourceObject(Base):
    __tablename__ = "collected_source_objects"
    __table_args__ = (
        UniqueConstraint("run_id", "source_object_id", "source_version", name="uq_run_source_object"),
        Index(
            "ix_collected_source_latest",
            "tenant_id",
            "connector_instance_id",
            "stream",
            "source_object_id",
        ),
    )

    collected_object_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("connector_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_instance_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.connector_instance_id", ondelete="CASCADE"), nullable=False
    )
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    source_object_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_version: Mapped[str] = mapped_column(String(512), nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    metadata_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
