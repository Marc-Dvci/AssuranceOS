"""Stored, digest-identified report versions.

Component 11. A report is a statement made at a moment, so it is stored whole
rather than as references to be reassembled: reassembling it later from records
that have since changed produces a different statement while claiming to be the
same one.

Preparation and issuance are separate columns because they are separate acts.
Rendering proves the claims are supported; issuing is the organisation deciding to
say them, and only a person may do the second.

Revision ID: 0013_report_versions
Revises: 0012_risk_assessment_and_planning
Create Date: 2026-08-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_report_versions"
down_revision: Union[str, None] = "0012_risk_assessment_and_planning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column("report_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "engagement_id",
            sa.String(length=64),
            sa.ForeignKey("engagements.engagement_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_type", sa.String(length=64), nullable=False),
        sa.Column("template_ref", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("material_claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limitation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prepared_by", sa.String(length=128), nullable=False),
        sa.Column("issued_by", sa.String(length=128), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issue_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "engagement_id", "report_type", "version", name="uq_report_version"
        ),
        sa.CheckConstraint("claim_count >= 0", name="claim_count_non_negative"),
    )
    op.create_index("ix_report_versions_tenant_id", "report_versions", ["tenant_id"])
    op.create_index("ix_report_versions_engagement_id", "report_versions", ["engagement_id"])


def downgrade() -> None:
    op.drop_index("ix_report_versions_engagement_id", table_name="report_versions")
    op.drop_index("ix_report_versions_tenant_id", table_name="report_versions")
    op.drop_table("report_versions")
