"""Governed continuous monitors and deduplicated review alerts.

Revision ID: 0014_continuous_monitoring
Revises: 0013_report_versions
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_continuous_monitoring"
down_revision: Union[str, None] = "0013_report_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "continuous_monitors",
        sa.Column("monitor_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("monitor_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("test_id", sa.String(128), nullable=False),
        sa.Column("test_version", sa.String(64), nullable=False),
        sa.Column("owner_ref", sa.String(255), nullable=False),
        sa.Column("reviewer_ref", sa.String(255), nullable=False),
        sa.Column("source_freshness_seconds", sa.Integer(), nullable=False),
        sa.Column("minimum_completeness", sa.Float(), nullable=False),
        sa.Column("exception_threshold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deduplication_window_seconds", sa.Integer(), nullable=False),
        sa.Column("alert_budget", sa.Integer(), nullable=False),
        sa.Column("response_sla_seconds", sa.Integer(), nullable=False),
        sa.Column("independence_preserved", sa.Boolean(), nullable=False),
        sa.Column("approval_ref", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suspended_reason", sa.Text()),
        sa.Column("configuration_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "monitor_key", "version", name="uq_monitor_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'suspended', 'retired')", name="monitor_status"
        ),
    )
    op.create_index("ix_continuous_monitors_tenant_id", "continuous_monitors", ["tenant_id"])
    op.create_table(
        "monitor_runs",
        sa.Column("monitor_run_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "monitor_id",
            sa.String(64),
            sa.ForeignKey("continuous_monitors.monitor_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "control_test_run_id",
            sa.String(64),
            sa.ForeignKey("control_test_runs.run_id", ondelete="SET NULL"),
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_completeness", sa.Float(), nullable=False),
        sa.Column("conclusion", sa.String(64), nullable=False),
        sa.Column("exception_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_monitor_run_idempotency"),
        sa.CheckConstraint(
            "status IN ('succeeded', 'suspended', 'failed')", name="monitor_run_status"
        ),
    )
    op.create_index("ix_monitor_runs_tenant_id", "monitor_runs", ["tenant_id"])
    op.create_index("ix_monitor_runs_monitor_id", "monitor_runs", ["monitor_id"])
    op.create_table(
        "monitor_alerts",
        sa.Column("alert_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "monitor_id",
            sa.String(64),
            sa.ForeignKey("continuous_monitors.monitor_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "monitor_run_id",
            sa.String(64),
            sa.ForeignKey("monitor_runs.monitor_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("deduplication_key", sa.String(255), nullable=False),
        sa.Column("exception_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_case_ref", sa.String(255)),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "monitor_id", "deduplication_key", name="uq_monitor_alert_dedup"
        ),
        sa.CheckConstraint(
            "status IN ('review_pending', 'acknowledged', 'resolved')", name="monitor_alert_status"
        ),
    )
    op.create_index("ix_monitor_alerts_tenant_id", "monitor_alerts", ["tenant_id"])
    op.create_index("ix_monitor_alerts_monitor_id", "monitor_alerts", ["monitor_id"])


def downgrade() -> None:
    op.drop_table("monitor_alerts")
    op.drop_table("monitor_runs")
    op.drop_table("continuous_monitors")
