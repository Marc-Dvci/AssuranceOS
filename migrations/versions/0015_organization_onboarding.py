"""Durable source-backed organization onboarding.

Revision ID: 0015_organization_onboarding
Revises: 0014_continuous_monitoring
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_organization_onboarding"
down_revision: Union[str, None] = "0014_continuous_monitoring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "onboarding_workflows",
        sa.Column("workflow_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("normalized_domain", sa.String(255)),
        sa.Column("headquarters_country", sa.String(2)),
        sa.Column("industry_hint", sa.String(128)),
        sa.Column(
            "profile_id",
            sa.String(64),
            sa.ForeignKey("organization_profiles.profile_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("remaining_unknowns_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("readiness_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "workflow_key", name="uq_onboarding_workflow_key"),
        sa.CheckConstraint(
            "status IN ('researching', 'profile_review', 'ready', 'approved')",
            name="onboarding_status",
        ),
    )
    op.create_index("ix_onboarding_workflows_tenant_id", "onboarding_workflows", ["tenant_id"])
    op.create_table(
        "public_source_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(64),
            sa.ForeignKey("onboarding_workflows.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("publisher", sa.String(255), nullable=False),
        sa.Column("source_quality", sa.String(32), nullable=False),
        sa.Column(
            "evidence_id",
            sa.String(64),
            sa.ForeignKey("evidence_records.evidence_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("excerpt_locator", sa.String(512)),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "content_sha256", "source_url", name="uq_public_snapshot_content"
        ),
        sa.CheckConstraint(
            "source_quality IN ('official', 'authoritative', 'reputable')",
            name="public_source_quality",
        ),
    )
    op.create_index(
        "ix_public_source_snapshots_tenant_id", "public_source_snapshots", ["tenant_id"]
    )
    op.create_index(
        "ix_public_source_snapshots_workflow_id", "public_source_snapshots", ["workflow_id"]
    )
    op.create_table(
        "organization_fact_decisions",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(64),
            sa.ForeignKey("onboarding_workflows.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fact_id",
            sa.String(64),
            sa.ForeignKey("organization_facts.fact_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "correction_fact_id",
            sa.String(64),
            sa.ForeignKey("organization_facts.fact_id", ondelete="SET NULL"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "decision IN ('accept', 'correct', 'not_applicable')", name="organization_fact_decision"
        ),
    )
    op.create_index(
        "ix_organization_fact_decisions_tenant_id", "organization_fact_decisions", ["tenant_id"]
    )
    op.create_index(
        "ix_organization_fact_decisions_fact_id", "organization_fact_decisions", ["fact_id"]
    )


def downgrade() -> None:
    op.drop_table("organization_fact_decisions")
    op.drop_table("public_source_snapshots")
    op.drop_table("onboarding_workflows")
