"""Agent identity, gateway enforcement, guardrail findings, and reasoning spans.

Revision ID: 0008_agent_governance
Revises: 0007_control_test_engine
Create Date: 2026-08-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_agent_governance"
down_revision: Union[str, None] = "0007_control_test_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_identities",
        sa.Column("identity_row_id", sa.String(length=64), nullable=False),
        sa.Column("identity_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workload_uri", sa.String(length=512), nullable=False),
        sa.Column("agent_role", sa.String(length=128), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=True),
        sa.Column("engagement_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("granted_tools_json", sa.JSON(), nullable=False),
        sa.Column("granted_scopes_json", sa.JSON(), nullable=False),
        sa.Column("forbidden_actions_json", sa.JSON(), nullable=False),
        sa.Column("independence_subject", sa.String(length=255), nullable=True),
        sa.Column("key_id", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('issued', 'revoked', 'expired')", name="ck_agent_identity_status"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["release_id"], ["agent_releases.release_id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["engagements.engagement_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("identity_row_id"),
        sa.UniqueConstraint("tenant_id", "identity_id", name="uq_agent_identity"),
    )
    op.create_index("ix_agent_identities_identity_id", "agent_identities", ["identity_id"])
    op.create_index("ix_agent_identities_tenant_id", "agent_identities", ["tenant_id"])
    op.create_index("ix_agent_identities_agent_role", "agent_identities", ["agent_role"])
    op.create_index("ix_agent_identities_engagement_id", "agent_identities", ["engagement_id"])
    op.create_index("ix_agent_identities_task", "agent_identities", ["tenant_id", "task_id"])

    op.create_table(
        "agent_gateway_decisions",
        sa.Column("decision_row_id", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("agent_role", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("engagement_id", sa.String(length=64), nullable=True),
        sa.Column("identity_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("decision IN ('allow', 'deny')", name="ck_gateway_decision_value"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["engagements.engagement_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("decision_row_id"),
        sa.UniqueConstraint("tenant_id", "decision_id", name="uq_gateway_decision"),
    )
    op.create_index(
        "ix_agent_gateway_decisions_decision_id", "agent_gateway_decisions", ["decision_id"]
    )
    op.create_index(
        "ix_agent_gateway_decisions_tenant_id", "agent_gateway_decisions", ["tenant_id"]
    )
    op.create_index("ix_agent_gateway_decisions_stage", "agent_gateway_decisions", ["stage"])
    op.create_index(
        "ix_agent_gateway_decisions_tool_name", "agent_gateway_decisions", ["tool_name"]
    )
    op.create_index(
        "ix_agent_gateway_decisions_engagement_id", "agent_gateway_decisions", ["engagement_id"]
    )
    op.create_index(
        "ix_agent_gateway_decisions_identity_id", "agent_gateway_decisions", ["identity_id"]
    )
    op.create_index(
        "ix_gateway_decisions_trace", "agent_gateway_decisions", ["tenant_id", "trace_id"]
    )
    op.create_index(
        "ix_gateway_decisions_task", "agent_gateway_decisions", ["tenant_id", "task_id"]
    )

    op.create_table(
        "agent_guardrail_findings",
        sa.Column("finding_row_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("detector", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("excerpt_digest", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('allow', 'redact', 'block')", name="ck_guardrail_verdict"
        ),
        sa.CheckConstraint(
            "direction IN ('inbound_context', 'tool_call', 'outbound_text')",
            name="ck_guardrail_direction",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("finding_row_id"),
    )
    op.create_index(
        "ix_agent_guardrail_findings_tenant_id", "agent_guardrail_findings", ["tenant_id"]
    )
    op.create_index(
        "ix_agent_guardrail_findings_decision_id", "agent_guardrail_findings", ["decision_id"]
    )
    op.create_index(
        "ix_agent_guardrail_findings_direction", "agent_guardrail_findings", ["direction"]
    )
    op.create_index(
        "ix_agent_guardrail_findings_verdict", "agent_guardrail_findings", ["verdict"]
    )
    op.create_index(
        "ix_agent_guardrail_findings_detector", "agent_guardrail_findings", ["detector"]
    )
    op.create_index(
        "ix_guardrail_findings_trace", "agent_guardrail_findings", ["tenant_id", "trace_id"]
    )

    op.create_table(
        "agent_reasoning_spans",
        sa.Column("span_row_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("parent_span_id", sa.String(length=32), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("engagement_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("agent_role", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("otel_exported", sa.Boolean(), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("events_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["engagements.engagement_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("span_row_id"),
        sa.UniqueConstraint("tenant_id", "trace_id", "span_id", name="uq_reasoning_span"),
    )
    op.create_index(
        "ix_agent_reasoning_spans_tenant_id", "agent_reasoning_spans", ["tenant_id"]
    )
    op.create_index("ix_agent_reasoning_spans_name", "agent_reasoning_spans", ["name"])
    op.create_index(
        "ix_agent_reasoning_spans_engagement_id", "agent_reasoning_spans", ["engagement_id"]
    )
    op.create_index("ix_agent_reasoning_spans_task_id", "agent_reasoning_spans", ["task_id"])
    op.create_index(
        "ix_reasoning_spans_trace", "agent_reasoning_spans", ["tenant_id", "trace_id"]
    )


def downgrade() -> None:
    op.drop_table("agent_reasoning_spans")
    op.drop_table("agent_guardrail_findings")
    op.drop_table("agent_gateway_decisions")
    op.drop_table("agent_identities")
