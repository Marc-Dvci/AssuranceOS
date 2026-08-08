"""Ensure finding skeptic-review columns exist on upgraded installations.

Some early 0.8 development databases applied revision 0010 before its two
skeptic-review columns were present. A migration revision is immutable once
applied, so this forward repair admits both histories and converges them on the
same schema.

Revision ID: 0016_finding_review_schema_consistency
Revises: 0015_organization_onboarding
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_finding_review_schema_consistency"
down_revision: Union[str, None] = "0015_organization_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("findings")}
    if "skeptic_reviewed_at" not in columns:
        op.add_column(
            "findings",
            sa.Column("skeptic_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "skeptic_rationale" not in columns:
        op.add_column("findings", sa.Column("skeptic_rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    # These columns are part of the 0010 schema for fresh installations. Removing
    # them here would make a downgrade depend on which historical 0010 ran.
    pass
