from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
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


class Standard(Base, TimestampMixin):
    """A versioned body of criteria.

    ``tenant_id`` is nullable. A published standard is the same document for every
    tenant, and copying it per tenant would make "did two engagements test against
    the same version" a question you answer by comparing text.
    """

    __tablename__ = "standards"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_standards_code_version"),)

    standard_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    licence: Mapped[str] = mapped_column(String(128), nullable=False, default="internal")
    entitlement_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class Criterion(Base, TimestampMixin):
    """One requirement inside a standard, with the citation that locates it."""

    __tablename__ = "criteria"
    __table_args__ = (UniqueConstraint("standard_id", "code", name="uq_criteria_standard_code"),)

    criterion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    standard_id: Mapped[str] = mapped_column(
        ForeignKey("standards.standard_id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    citation: Mapped[str] = mapped_column(String(512), nullable=False)
    strength: Mapped[str] = mapped_column(String(32), nullable=False, default="mandatory")
    requirement_ref: Mapped[str | None] = mapped_column(String(128))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class CriteriaCrosswalk(Base):
    """An asserted relationship between criteria in two standards.

    Carries who asserted it. An assurance map assembled from unattributed
    equivalences is a map of what somebody assumed.
    """

    __tablename__ = "criteria_crosswalks"
    __table_args__ = (
        UniqueConstraint(
            "source_criterion_id", "target_criterion_id", "relation", name="uq_crosswalk_edge"
        ),
    )

    crosswalk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_criterion_id: Mapped[str] = mapped_column(
        ForeignKey("criteria.criterion_id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_criterion_id: Mapped[str] = mapped_column(
        ForeignKey("criteria.criterion_id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    asserted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    asserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    change_impact_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class CriteriaMapping(Base):
    """A criterion's link to a risk, control, procedure, or deterministic test."""

    __tablename__ = "criteria_mappings"
    __table_args__ = (
        UniqueConstraint(
            "criterion_id", "target_type", "target_ref", name="uq_criteria_mapping_target"
        ),
    )

    mapping_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), index=True
    )
    criterion_id: Mapped[str] = mapped_column(
        ForeignKey("criteria.criterion_id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    coverage: Mapped[str] = mapped_column(String(32), nullable=False, default="partial")
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class StandardEntitlement(Base):
    """A tenant's licence to have a standard's text reproduced for it."""

    __tablename__ = "standard_entitlements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "standard_code", name="uq_entitlement_tenant_standard"),
    )

    entitlement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    standard_code: Mapped[str] = mapped_column(String(64), nullable=False)
    licence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_on: Mapped[date | None] = mapped_column(Date)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditPackRegistration(Base, TimestampMixin):
    """A signed Audit Pack admitted to the platform.

    The digest is the identity. A pack registered at one digest and later modified
    on disk is a different pack, and registration recording the digest is what lets
    a compilation say which one it used.
    """

    __tablename__ = "audit_pack_registrations"
    __table_args__ = (
        UniqueConstraint("pack_id", "version", name="uq_pack_registration_version"),
    )

    registration_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="registered")
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    release_key_id: Mapped[str | None] = mapped_column(String(128))
    standard_code: Mapped[str] = mapped_column(String(64), nullable=False)
    standard_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    compatibility_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    registered_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    superseded_by: Mapped[str | None] = mapped_column(String(64))


class PackCompilation(Base):
    """A record of one pack becoming one engagement's task graph.

    The pins are the point. A later pack version compiles into a new engagement
    and leaves this one alone, and the way you demonstrate that is by reading the
    digest this compilation was pinned to rather than by trusting that nothing
    mutated.
    """

    __tablename__ = "pack_compilations"
    __table_args__ = (
        UniqueConstraint("engagement_id", name="uq_pack_compilation_engagement"),
        CheckConstraint("task_count > 0", name="task_count_positive"),
    )

    compilation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), nullable=False, index=True
    )
    registration_id: Mapped[str] = mapped_column(
        ForeignKey("audit_pack_registrations.registration_id", ondelete="RESTRICT"),
        nullable=False,
    )
    pack_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pack_version: Mapped[str] = mapped_column(String(32), nullable=False)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pins_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    pins_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_context_json: Mapped[JsonObject] = mapped_column(
        JSON, nullable=False, default=dict
    )
    task_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    compiled_by: Mapped[str] = mapped_column(String(128), nullable=False)
    compiled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
