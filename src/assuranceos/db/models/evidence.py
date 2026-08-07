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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, utc_now
from .common import JsonObject, TimestampMixin


class EvidenceRecord(Base):
    """Canonical identity and provenance for one evidence acquisition.

    Physical objects are content-addressed and may be shared by several records in the same
    tenant. A record remains distinct because source, acquisition time, scope, and custody are
    evidentiary facts even when the bytes are identical.
    """

    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "acquisition_key", name="uq_evidence_acquisition_key"),
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint(
            "record_kind IN ('original', 'derived')", name="valid_record_kind"
        ),
        CheckConstraint(
            "integrity_status IN ('unverified', 'verified', 'mismatch', 'missing', 'purged')",
            name="valid_integrity_status",
        ),
        Index("ix_evidence_source_digest", "tenant_id", "source_locator", "content_sha256"),
        Index("ix_evidence_active_object", "tenant_id", "storage_key", "deleted_at"),
    )

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagement_tasks.task_id", ondelete="SET NULL"), index=True
    )
    acquisition_key: Mapped[str | None] = mapped_column(String(255))
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="original")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    storage_key: Mapped[str | None] = mapped_column(Text)
    object_uri: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    content_encoding: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classification: Mapped[str] = mapped_column(String(64), nullable=False, default="internal")
    source_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tainted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    integrity_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[date | None] = mapped_column(Date)
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class EvidenceTransformation(Base):
    __tablename__ = "evidence_transformations"
    __table_args__ = (UniqueConstraint("source_evidence_id", "derived_evidence_id", "operation"),)

    transformation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="CASCADE"), nullable=False
    )
    derived_evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    parameters_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class EvidenceCustodyEvent(Base):
    """Append-only, hash-chained custody event for one evidence record."""

    __tablename__ = "evidence_custody_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "evidence_id", "sequence_no", name="uq_evidence_custody_sequence"
        ),
        UniqueConstraint("tenant_id", "event_hash", name="uq_evidence_custody_hash"),
        CheckConstraint("sequence_no > 0", name="positive_sequence"),
    )

    custody_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    details_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Claim(Base, TimestampMixin):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagement_tasks.task_id", ondelete="SET NULL"), index=True
    )
    finding_id: Mapped[str | None] = mapped_column(
        ForeignKey("findings.finding_id", ondelete="SET NULL"), index=True
    )
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    limitations_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class ClaimEvidenceLink(Base):
    __tablename__ = "claim_evidence_links"
    __table_args__ = (UniqueConstraint("claim_id", "evidence_id", "relationship"),)

    link_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("claims.claim_id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="CASCADE"), nullable=False
    )
    relationship: Mapped[str] = mapped_column(String(32), nullable=False, default="supports")
    rationale: Mapped[str | None] = mapped_column(Text)
