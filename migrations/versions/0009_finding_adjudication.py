"""Finding adjudication, remediation, and independent retest.

The lifecycle tables already existed as contract shapes. This revision adds the
columns the state machine needs to be attributable: what evidence a finding rests
on, what the skeptic found against it, who authored it, which external ticket a
remediation opened, and what independence basis a retest was accepted under.

Revision ID: 0009_finding_adjudication
Revises: 0008_agent_governance
Create Date: 2026-08-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_finding_adjudication"
down_revision: Union[str, None] = "0008_agent_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows need a value before the column can be non-nullable, so each
    # JSON column is added with a server default, backfilled implicitly by that
    # default, and left with it in place for future inserts made outside the ORM.
    op.add_column(
        "findings",
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "findings",
        sa.Column("contradictions_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "findings",
        sa.Column("exception_keys_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("findings", sa.Column("source_run_id", sa.String(length=64), nullable=True))
    op.add_column("findings", sa.Column("authored_by", sa.String(length=128), nullable=True))
    op.create_index("ix_findings_code", "findings", ["tenant_id", "code"])

    op.add_column(
        "remediation_actions",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "remediation_actions",
        sa.Column("external_system", sa.String(length=32), nullable=False, server_default="none"),
    )
    op.add_column(
        "remediation_actions", sa.Column("external_ref", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "remediation_actions", sa.Column("completed_by", sa.String(length=128), nullable=True)
    )
    # One open remediation per finding, enforced by the database rather than by
    # the service remembering to check. A replay that reaches the insert still
    # cannot create a second ticket.
    op.create_index(
        "uq_remediation_action_finding",
        "remediation_actions",
        ["finding_id"],
        unique=True,
    )

    op.add_column(
        "management_responses",
        sa.Column("closure_evidence_ids_json", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.add_column(
        "retests",
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("retests", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column(
        "retests",
        sa.Column("independence_basis_json", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("retests", "independence_basis_json")
    op.drop_column("retests", "idempotency_key")
    op.drop_column("retests", "evidence_ids_json")

    op.drop_column("management_responses", "closure_evidence_ids_json")

    op.drop_index("uq_remediation_action_finding", table_name="remediation_actions")
    op.drop_column("remediation_actions", "completed_by")
    op.drop_column("remediation_actions", "external_ref")
    op.drop_column("remediation_actions", "external_system")
    op.drop_column("remediation_actions", "idempotency_key")

    op.drop_index("ix_findings_code", table_name="findings")
    op.drop_column("findings", "authored_by")
    op.drop_column("findings", "source_run_id")
    op.drop_column("findings", "exception_keys_json")
    op.drop_column("findings", "contradictions_json")
    op.drop_column("findings", "evidence_ids_json")
