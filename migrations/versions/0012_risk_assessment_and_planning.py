"""Risk assessments, assurance coverage, and plan proposals.

Component 10. Ratings become versioned computations rather than a mutable column,
assurance obtained elsewhere becomes a first-class record so it can lower the need
for fresh work without lowering the risk, and the plan becomes a proposal that
carries its own exclusions so an approval of it is an informed one.

Revision ID: 0012_risk_assessment_and_planning
Revises: 0011_standards_and_audit_packs
Create Date: 2026-08-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_risk_assessment_and_planning"
down_revision: Union[str, None] = "0011_standards_and_audit_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic creates ``alembic_version.version_num`` as VARCHAR(32). This
    # revision and later descriptive revision IDs are longer, and PostgreSQL
    # enforces that bound when Alembic records the completed migration. SQLite
    # neither enforces VARCHAR lengths nor supports this ALTER COLUMN form.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    op.create_table(
        "risk_assessments",
        sa.Column("assessment_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "risk_id",
            sa.String(length=64),
            sa.ForeignKey("risks.risk_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("factors_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("components_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("inherent", sa.Float(), nullable=False),
        sa.Column("residual", sa.Float(), nullable=False),
        sa.Column("rating", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("audit_priority", sa.Float(), nullable=False),
        sa.Column("uncovered", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("assessed_by", sa.String(length=128), nullable=False),
        # The date the score was computed *as at*, distinct from when it was
        # recorded. Ratings are recomputed retrospectively often enough that
        # conflating the two makes staleness unmeasurable.
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("official_rating", sa.String(length=32), nullable=True),
        sa.Column("official_reason", sa.Text(), nullable=True),
        sa.Column("official_by", sa.String(length=128), nullable=True),
        sa.UniqueConstraint("risk_id", "version", name="uq_risk_assessment_version"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.CheckConstraint("residual >= 0 AND residual <= 1", name="residual_range"),
    )
    op.create_index("ix_risk_assessments_tenant_id", "risk_assessments", ["tenant_id"])
    op.create_index("ix_risk_assessments_risk_id", "risk_assessments", ["risk_id"])

    op.create_table(
        "assurance_coverage",
        sa.Column("coverage_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "risk_id",
            sa.String(length=64),
            sa.ForeignKey("risks.risk_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            sa.String(length=64),
            sa.ForeignKey("audit_universe_entities.entity_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("obtained_on", sa.Date(), nullable=False),
        sa.Column("scope_note", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column(
            "engagement_id",
            sa.String(length=64),
            sa.ForeignKey("engagements.engagement_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recorded_by", sa.String(length=128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assurance_coverage_tenant_id", "assurance_coverage", ["tenant_id"])
    op.create_index("ix_assurance_coverage_risk_id", "assurance_coverage", ["risk_id"])
    op.create_index("ix_assurance_coverage_entity_id", "assurance_coverage", ["entity_id"])

    op.create_table(
        "plan_proposals",
        sa.Column("proposal_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(length=64),
            sa.ForeignKey("audit_plans.plan_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
        sa.Column("scenario", sa.String(length=64), nullable=False, server_default="baseline"),
        sa.Column("horizon_start", sa.Date(), nullable=False),
        sa.Column("horizon_end", sa.Date(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("planned_json", sa.JSON(), nullable=False, server_default="[]"),
        # The exclusions travel with the proposal rather than being derived later.
        # An approval given without seeing what was left out is not informed.
        sa.Column("excluded_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("blind_spots_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("planned_days", sa.Float(), nullable=False),
        sa.Column("plannable_days", sa.Float(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("uncovered_priority", sa.Float(), nullable=False),
        sa.Column("notes_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("proposed_by", sa.String(length=128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("accepted_residual_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", "version", name="uq_plan_proposal_version"),
        sa.CheckConstraint("planned_days >= 0", name="planned_days_non_negative"),
    )
    op.create_index("ix_plan_proposals_tenant_id", "plan_proposals", ["tenant_id"])
    op.create_index("ix_plan_proposals_plan_id", "plan_proposals", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_proposals_plan_id", table_name="plan_proposals")
    op.drop_index("ix_plan_proposals_tenant_id", table_name="plan_proposals")
    op.drop_table("plan_proposals")
    op.drop_index("ix_assurance_coverage_entity_id", table_name="assurance_coverage")
    op.drop_index("ix_assurance_coverage_risk_id", table_name="assurance_coverage")
    op.drop_index("ix_assurance_coverage_tenant_id", table_name="assurance_coverage")
    op.drop_table("assurance_coverage")
    op.drop_index("ix_risk_assessments_risk_id", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_id", table_name="risk_assessments")
    op.drop_table("risk_assessments")
