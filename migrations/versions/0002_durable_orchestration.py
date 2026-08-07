"""Add durable orchestration task fields.

Revision ID: 0002_durable_orchestration
Revises: 0001_canonical_domain
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_durable_orchestration"
down_revision: Union[str, None] = "fa73e07500b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.add_column(sa.Column("stream_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("sequence_no", sa.Integer(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT event_id, tenant_id, engagement_id, task_id "
            "FROM audit_events ORDER BY occurred_at, event_id"
        )
    ).mappings()
    counters: dict[tuple[str, str], int] = {}
    for row in rows:
        stream_id = row["task_id"] or row["engagement_id"] or f"tenant:{row['tenant_id']}"
        key = (row["tenant_id"], stream_id)
        sequence_no = counters.get(key, 0) + 1
        counters[key] = sequence_no
        connection.execute(
            sa.text(
                "UPDATE audit_events SET stream_id=:stream_id, sequence_no=:sequence_no "
                "WHERE event_id=:event_id"
            ),
            {
                "stream_id": stream_id,
                "sequence_no": sequence_no,
                "event_id": row["event_id"],
            },
        )

    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.alter_column("stream_id", existing_type=sa.String(length=128), nullable=False)
        batch_op.alter_column("sequence_no", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_audit_events_stream_id", ["stream_id"], unique=False)
        batch_op.create_unique_constraint(
            "uq_audit_events_tenant_id", ["tenant_id", "stream_id", "sequence_no"]
        )

    with op.batch_alter_table("engagement_tasks") as batch_op:
        batch_op.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
        batch_op.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("result_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch_op.drop_index("ix_tasks_claimable")
        batch_op.create_index(
            "ix_tasks_claimable",
            ["status", "available_at", "priority", "lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("engagement_tasks") as batch_op:
        batch_op.drop_index("ix_tasks_claimable")
        batch_op.create_index(
            "ix_tasks_claimable", ["status", "lease_expires_at"], unique=False
        )
        batch_op.drop_column("last_error")
        batch_op.drop_column("result_json")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("deadline_at")
        batch_op.drop_column("available_at")
        batch_op.drop_column("priority")

    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_constraint("uq_audit_events_tenant_id", type_="unique")
        batch_op.drop_index("ix_audit_events_stream_id")
        batch_op.drop_column("sequence_no")
        batch_op.drop_column("stream_id")
