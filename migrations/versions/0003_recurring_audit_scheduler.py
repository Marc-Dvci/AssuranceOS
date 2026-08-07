"""Add recurring audit scheduler state and occurrence provenance.

Revision ID: 0003_recurring_scheduler
Revises: 0002_durable_orchestration
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_recurring_scheduler"
down_revision: Union[str, None] = "0002_durable_orchestration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()

    with op.batch_alter_table("engagement_templates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "workflow_definition_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    with op.batch_alter_table("audit_schedules") as batch_op:
        batch_op.add_column(sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("business_calendar_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(
            sa.Column("missed_occurrence_policy", sa.String(length=32), nullable=False, server_default="launch_latest")
        )
        batch_op.add_column(sa.Column("catch_up_limit", sa.Integer(), nullable=False, server_default="12"))
        batch_op.add_column(
            sa.Column("overlap_policy", sa.String(length=32), nullable=False, server_default="prevent")
        )
        batch_op.add_column(
            sa.Column("max_concurrent_engagements", sa.Integer(), nullable=False, server_default="1")
        )

    connection.execute(
        sa.text("UPDATE audit_schedules SET effective_from = created_at WHERE effective_from IS NULL")
    )
    with op.batch_alter_table("audit_schedules") as batch_op:
        batch_op.alter_column(
            "effective_from", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.create_check_constraint(
            "ck_audit_schedules_effective_window",
            "effective_until IS NULL OR effective_until >= effective_from",
        )
        batch_op.create_check_constraint(
            "ck_audit_schedules_positive_catch_up_limit", "catch_up_limit > 0"
        )
        batch_op.create_check_constraint(
            "ck_audit_schedules_positive_concurrency_limit",
            "max_concurrent_engagements > 0",
        )

    op.create_table(
        "schedule_cursors",
        sa.Column("schedule_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["audit_schedules.schedule_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("schedule_id"),
    )
    op.create_index("ix_schedule_cursors_tenant_id", "schedule_cursors", ["tenant_id"])
    op.create_index("ix_schedule_cursors_next_due_at", "schedule_cursors", ["next_due_at"])

    with op.batch_alter_table("schedule_occurrences") as batch_op:
        batch_op.add_column(sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("period_start", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("period_end", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("decision_by", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("template_version", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("schedule_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(
            sa.Column("template_snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(
            sa.Column("preflight_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch_op.add_column(sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("launch_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    connection.execute(
        sa.text(
            "UPDATE schedule_occurrences SET "
            "eligible_at = nominal_due, "
            "period_start = DATE(nominal_due), "
            "period_end = DATE(nominal_due), "
            "updated_at = created_at, "
            "template_version = COALESCE(("
            "  SELECT et.version FROM audit_schedules s "
            "  JOIN engagement_templates et ON et.template_id = s.template_id "
            "  WHERE s.schedule_id = schedule_occurrences.schedule_id"
            "), 1)"
        )
    )
    with op.batch_alter_table("schedule_occurrences") as batch_op:
        batch_op.alter_column("eligible_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column("period_start", existing_type=sa.Date(), nullable=False)
        batch_op.alter_column("period_end", existing_type=sa.Date(), nullable=False)
        batch_op.alter_column("template_version", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.create_check_constraint(
            "ck_schedule_occurrences_period_order", "period_end >= period_start"
        )


def downgrade() -> None:
    with op.batch_alter_table("schedule_occurrences") as batch_op:
        batch_op.drop_constraint("ck_schedule_occurrences_period_order", type_="check")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("launch_attempts")
        batch_op.drop_column("launched_at")
        batch_op.drop_column("evaluated_at")
        batch_op.drop_column("preflight_result_json")
        batch_op.drop_column("template_snapshot_json")
        batch_op.drop_column("schedule_snapshot_json")
        batch_op.drop_column("template_version")
        batch_op.drop_column("decision_by")
        batch_op.drop_column("period_end")
        batch_op.drop_column("period_start")
        batch_op.drop_column("eligible_at")

    op.drop_index("ix_schedule_cursors_next_due_at", table_name="schedule_cursors")
    op.drop_index("ix_schedule_cursors_tenant_id", table_name="schedule_cursors")
    op.drop_table("schedule_cursors")

    with op.batch_alter_table("audit_schedules") as batch_op:
        batch_op.drop_constraint(
            "ck_audit_schedules_positive_concurrency_limit", type_="check"
        )
        batch_op.drop_constraint(
            "ck_audit_schedules_positive_catch_up_limit", type_="check"
        )
        batch_op.drop_constraint("ck_audit_schedules_effective_window", type_="check")
        batch_op.drop_column("max_concurrent_engagements")
        batch_op.drop_column("overlap_policy")
        batch_op.drop_column("catch_up_limit")
        batch_op.drop_column("missed_occurrence_policy")
        batch_op.drop_column("business_calendar_json")
        batch_op.drop_column("effective_until")
        batch_op.drop_column("effective_from")

    with op.batch_alter_table("engagement_templates") as batch_op:
        batch_op.drop_column("workflow_definition_json")

