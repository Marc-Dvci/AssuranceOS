from __future__ import annotations

from datetime import datetime
from typing import Any

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


class OrganizationProfile(Base, TimestampMixin):
    __tablename__ = "organization_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_domain: Mapped[str | None] = mapped_column(String(255))
    headquarters_country: Mapped[str | None] = mapped_column(String(2))
    industry: Mapped[str | None] = mapped_column(String(128))
    canonical_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationFact(Base):
    __tablename__ = "organization_facts"
    __table_args__ = (UniqueConstraint("profile_id", "fact_key"),)

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("organization_profiles.profile_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AuditUniverseEntity(Base, TimestampMixin):
    __tablename__ = "audit_universe_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_type", "external_ref"),
        CheckConstraint("criticality >= 0 AND criticality <= 5", name="criticality_range"),
    )

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization_profiles.profile_id", ondelete="SET NULL"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    criticality: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class EntityRelationship(Base):
    __tablename__ = "entity_relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type", "valid_from"),
    )

    relationship_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_entity_id: Mapped[str] = mapped_column(
        ForeignKey("audit_universe_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        ForeignKey("audit_universe_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Risk(Base, TimestampMixin):
    __tablename__ = "risks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    risk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    inherent_impact: Mapped[float | None] = mapped_column(Float)
    inherent_likelihood: Mapped[float | None] = mapped_column(Float)
    velocity: Mapped[float | None] = mapped_column(Float)
    control_maturity: Mapped[float | None] = mapped_column(Float)
    residual_risk: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    evidence_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class Control(Base, TimestampMixin):
    __tablename__ = "controls"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    control_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_ref: Mapped[str | None] = mapped_column(String(255))
    frequency: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    attributes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class RiskControlLink(Base):
    __tablename__ = "risk_control_links"
    __table_args__ = (UniqueConstraint("risk_id", "control_id"),)

    link_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_id: Mapped[str] = mapped_column(
        ForeignKey("risks.risk_id", ondelete="CASCADE"), nullable=False
    )
    control_id: Mapped[str] = mapped_column(
        ForeignKey("controls.control_id", ondelete="CASCADE"), nullable=False
    )
    coverage: Mapped[str] = mapped_column(String(32), nullable=False, default="partial")
    rationale: Mapped[str | None] = mapped_column(Text)
