"""Production hardening for attempts, outbox delivery, and schedule governance.

Revision ID: 0006_production_hardening
Revises: 0005_connector_sdk
Create Date: 2026-08-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_production_hardening"
down_revision: Union[str, None] = "0005_connector_sdk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_attempts",
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("engagement_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("output_refs_json", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint("attempt_no > 0", name="ck_task_attempts_positive_attempt_number"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retry_scheduled', 'failed', "
            "'lease_expired', 'cancelled')",
            name="ck_task_attempts_valid_attempt_status",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["engagements.engagement_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["engagement_tasks.task_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt_number"),
    )
    op.create_index(
        "ix_task_attempts_engagement",
        "task_attempts",
        ["tenant_id", "engagement_id", "started_at"],
    )
    op.create_index("ix_task_attempts_engagement_id", "task_attempts", ["engagement_id"])
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.create_index("ix_task_attempts_tenant_id", "task_attempts", ["tenant_id"])
    op.create_index("ix_task_attempts_trace_id", "task_attempts", ["trace_id"])

    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    with op.batch_alter_table("outbox_events") as batch:
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_owner", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("published_message_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE outbox_events SET available_at = occurred_at WHERE available_at IS NULL"))
    with op.batch_alter_table("outbox_events") as batch:
        batch.alter_column("available_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.create_index(
        "ix_outbox_claimable",
        "outbox_events",
        ["published_at", "dead_lettered_at", "available_at", "lease_expires_at"],
    )
    op.create_index("ix_outbox_events_available_at", "outbox_events", ["available_at"])

    with op.batch_alter_table("audit_schedules") as batch:
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("approved_by", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("approval_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("disabled_by", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("disable_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audit_schedules") as batch:
        batch.drop_column("disable_reason")
        batch.drop_column("disabled_by")
        batch.drop_column("disabled_at")
        batch.drop_column("approval_reason")
        batch.drop_column("approved_by")
        batch.drop_column("approved_at")

    op.drop_index("ix_outbox_events_available_at", table_name="outbox_events")
    op.drop_index("ix_outbox_claimable", table_name="outbox_events")
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_column("dead_lettered_at")
        batch.drop_column("published_message_id")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
        batch.drop_column("available_at")
    op.create_index(
        "ix_outbox_unpublished", "outbox_events", ["published_at", "occurred_at"]
    )

    op.drop_index("ix_task_attempts_trace_id", table_name="task_attempts")
    op.drop_index("ix_task_attempts_tenant_id", table_name="task_attempts")
    op.drop_index("ix_task_attempts_task_id", table_name="task_attempts")
    op.drop_index("ix_task_attempts_engagement_id", table_name="task_attempts")
    op.drop_index("ix_task_attempts_engagement", table_name="task_attempts")
    op.drop_table("task_attempts")
