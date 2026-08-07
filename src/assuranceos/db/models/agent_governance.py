"""Canonical state for agent identity, gateway enforcement, guardrails, and traces.

These records are evidence about how the fleet behaved, so they are written to the
canonical store rather than left to logs. A denial that exists only in a log line
cannot be reconstructed during an audit of the auditor.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
from .common import JsonObject


class AgentIdentityRecord(Base):
    """One short-lived workload credential issued by the control plane."""

    __tablename__ = "agent_identities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "identity_id", name="uq_agent_identity"),
        CheckConstraint(
            "status IN ('issued', 'revoked', 'expired')", name="ck_agent_identity_status"
        ),
        Index("ix_agent_identities_task", "tenant_id", "task_id"),
    )

    identity_row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    workload_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    release_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_releases.release_id", ondelete="SET NULL")
    )
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    granted_tools_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=list)
    granted_scopes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=list)
    forbidden_actions_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=list)
    independence_subject: Mapped[str | None] = mapped_column(String(255))
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GatewayDecisionRecord(Base):
    """Every allow and deny the Agent Gateway produced, with its trace linkage."""

    __tablename__ = "agent_gateway_decisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "decision_id", name="uq_gateway_decision"),
        CheckConstraint("decision IN ('allow', 'deny')", name="ck_gateway_decision_value"),
        Index("ix_gateway_decisions_trace", "tenant_id", "trace_id"),
        Index("ix_gateway_decisions_task", "tenant_id", "task_id"),
    )

    decision_row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    agent_role: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64))
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), index=True
    )
    identity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attributes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)


class GuardrailFindingRecord(Base):
    """A Model Armor verdict on one screened boundary crossing."""

    __tablename__ = "agent_guardrail_findings"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('allow', 'redact', 'block')", name="ck_guardrail_verdict"
        ),
        CheckConstraint(
            "direction IN ('inbound_context', 'tool_call', 'outbound_text')",
            name="ck_guardrail_direction",
        ),
        Index("ix_guardrail_findings_trace", "tenant_id", "trace_id"),
    )

    finding_row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision_id: Mapped[str | None] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    detector: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Matched content is never persisted; a digest keeps findings correlatable.
    excerpt_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ReasoningSpanRecord(Base):
    """One span of a persisted agent reasoning chain."""

    __tablename__ = "agent_reasoning_spans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "trace_id", "span_id", name="uq_reasoning_span"),
        Index("ix_reasoning_spans_trace", "tenant_id", "trace_id"),
    )

    span_row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(32))
    # Creation order. Timestamps collide below microsecond resolution, so this is
    # what makes a rebuilt reasoning chain reproduce the original step order.
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    agent_role: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unset")
    status_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    otel_exported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attributes_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=dict)
    events_json: Mapped[JsonObject] = mapped_column(JSON, nullable=False, default=list)
