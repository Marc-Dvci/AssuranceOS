"""Materiality, quality review, dispute rounds, and external remediation tickets.

Component 7 delivered the lifecycle. This revision adds the three gates that were
designed for but deferred — a scored materiality step, a methodology review held
separately from approval, and a numbered dispute history — plus the columns a
remediation needs to be reconciled against Jira or ServiceNow rather than merely
labelled with the name of one.

Each new table carries ``content_hash``. Binding a gate to the digest of the text
it was passed against is what makes "this finding was reviewed" survive the
finding being edited afterwards.

Revision ID: 0010_assurance_review_gates
Revises: 0009_finding_adjudication
Create Date: 2026-08-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_assurance_review_gates"
down_revision: Union[str, None] = "0009_finding_adjudication"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "materiality_assessments",
        sa.Column("assessment_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            sa.String(length=64),
            sa.ForeignKey("findings.finding_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("population_size", sa.Integer(), nullable=False),
        sa.Column("exception_count", sa.Integer(), nullable=False),
        sa.Column("monetary_exposure", sa.Float(), nullable=True),
        sa.Column("factors_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("components_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("material", sa.Boolean(), nullable=False),
        sa.Column("severity_floor", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("assessed_by", sa.String(length=128), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("override_severity", sa.String(length=32), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_by", sa.String(length=128), nullable=True),
        sa.CheckConstraint("score >= 0", name="score_non_negative"),
        sa.CheckConstraint("population_size >= 0", name="population_non_negative"),
        sa.CheckConstraint("exception_count >= 0", name="exception_count_non_negative"),
    )
    op.create_index(
        "ix_materiality_assessments_tenant_id", "materiality_assessments", ["tenant_id"]
    )
    op.create_index(
        "ix_materiality_assessments_finding_id", "materiality_assessments", ["finding_id"]
    )
    # The approval gate looks up "is there an assessment of *this* text", so the
    # index carries the hash rather than the finding alone.
    op.create_index(
        "ix_materiality_assessments_content",
        "materiality_assessments",
        ["finding_id", "content_hash"],
    )

    op.create_table(
        "quality_reviews",
        sa.Column("review_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            sa.String(length=64),
            sa.ForeignKey("findings.finding_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewer_id", sa.String(length=128), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("checks_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quality_reviews_tenant_id", "quality_reviews", ["tenant_id"])
    op.create_index("ix_quality_reviews_finding_id", "quality_reviews", ["finding_id"])
    op.create_index(
        "ix_quality_reviews_content", "quality_reviews", ["finding_id", "content_hash"]
    )

    op.create_table(
        "finding_disputes",
        sa.Column("dispute_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            sa.String(length=64),
            sa.ForeignKey("findings.finding_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("ground", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("raised_by", sa.String(length=128), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("prior_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("finding_id", "round_no", name="uq_finding_disputes_round"),
    )
    op.create_index("ix_finding_disputes_tenant_id", "finding_disputes", ["tenant_id"])
    op.create_index("ix_finding_disputes_finding_id", "finding_disputes", ["finding_id"])

    # An empty contradiction list means either "searched and found nothing" or
    # "never searched". The quality gate has to distinguish them, so when the
    # search ran becomes a fact on the finding rather than an inference from the
    # absence of results. Existing rows keep NULL, which reads as "not searched" —
    # the conservative direction for a gate.
    op.add_column(
        "findings", sa.Column("skeptic_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("findings", sa.Column("skeptic_rationale", sa.Text(), nullable=True))

    # Existing remediation rows predate external filing. ``not_applicable`` is the
    # honest default for them: they were never registered against a provider, and
    # marking them ``pending`` would enqueue historic actions for a sync that
    # should not happen.
    op.add_column(
        "remediation_actions", sa.Column("external_target", sa.String(length=128), nullable=True)
    )
    op.add_column("remediation_actions", sa.Column("external_url", sa.Text(), nullable=True))
    op.add_column(
        "remediation_actions",
        sa.Column(
            "external_sync_state",
            sa.String(length=32),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "remediation_actions",
        sa.Column("external_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("remediation_actions", sa.Column("external_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("remediation_actions", "external_error")
    op.drop_column("remediation_actions", "external_synced_at")
    op.drop_column("remediation_actions", "external_sync_state")
    op.drop_column("remediation_actions", "external_url")
    op.drop_column("remediation_actions", "external_target")
    op.drop_column("findings", "skeptic_rationale")
    op.drop_column("findings", "skeptic_reviewed_at")
    op.drop_index("ix_finding_disputes_finding_id", table_name="finding_disputes")
    op.drop_index("ix_finding_disputes_tenant_id", table_name="finding_disputes")
    op.drop_table("finding_disputes")
    op.drop_index("ix_quality_reviews_content", table_name="quality_reviews")
    op.drop_index("ix_quality_reviews_finding_id", table_name="quality_reviews")
    op.drop_index("ix_quality_reviews_tenant_id", table_name="quality_reviews")
    op.drop_table("quality_reviews")
    op.drop_index("ix_materiality_assessments_content", table_name="materiality_assessments")
    op.drop_index("ix_materiality_assessments_finding_id", table_name="materiality_assessments")
    op.drop_index("ix_materiality_assessments_tenant_id", table_name="materiality_assessments")
    op.drop_table("materiality_assessments")
