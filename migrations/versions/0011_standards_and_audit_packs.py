"""Standards, criteria, crosswalks, entitlements, pack registrations, compilations.

Component 8. The tables that let an engagement's methodology be a compiled
artefact rather than a hand-authored task list: versioned criteria with the
citation that locates them, crosswalks between frameworks, per-tenant
entitlements for licensed standards, the registry of admitted Audit Packs, and
the compilation record that pins every version a task graph depended on.

Revision ID: 0011_standards_and_audit_packs
Revises: 0010_assurance_review_gates
Create Date: 2026-08-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_standards_and_audit_packs"
down_revision: Union[str, None] = "0010_assurance_review_gates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `tenant_id` is nullable: a published standard is the same document for
    # every tenant, and copying it per tenant would turn "did two engagements
    # test against the same version" into a text comparison.
    op.create_table(
        "standards",
        sa.Column("standard_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction", sa.String(length=64), nullable=False, server_default="global"),
        sa.Column("licence", sa.String(length=128), nullable=False, server_default="internal"),
        sa.Column(
            "entitlement_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", "version", name="uq_standards_code_version"),
    )
    op.create_index("ix_standards_tenant_id", "standards", ["tenant_id"])

    op.create_table(
        "criteria",
        sa.Column("criterion_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "standard_id",
            sa.String(length=64),
            sa.ForeignKey("standards.standard_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("citation", sa.String(length=512), nullable=False),
        sa.Column("strength", sa.String(length=32), nullable=False, server_default="mandatory"),
        sa.Column("requirement_ref", sa.String(length=128), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("standard_id", "code", name="uq_criteria_standard_code"),
    )
    op.create_index("ix_criteria_standard_id", "criteria", ["standard_id"])

    op.create_table(
        "criteria_crosswalks",
        sa.Column("crosswalk_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "source_criterion_id",
            sa.String(length=64),
            sa.ForeignKey("criteria.criterion_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_criterion_id",
            sa.String(length=64),
            sa.ForeignKey("criteria.criterion_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("asserted_by", sa.String(length=128), nullable=False),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_impact_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "source_criterion_id", "target_criterion_id", "relation", name="uq_crosswalk_edge"
        ),
    )
    op.create_index(
        "ix_criteria_crosswalks_source", "criteria_crosswalks", ["source_criterion_id"]
    )
    op.create_index(
        "ix_criteria_crosswalks_target", "criteria_crosswalks", ["target_criterion_id"]
    )

    op.create_table(
        "criteria_mappings",
        sa.Column("mapping_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "criterion_id",
            sa.String(length=64),
            sa.ForeignKey("criteria.criterion_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column("coverage", sa.String(length=32), nullable=False, server_default="partial"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "criterion_id", "target_type", "target_ref", name="uq_criteria_mapping_target"
        ),
    )
    op.create_index("ix_criteria_mappings_tenant_id", "criteria_mappings", ["tenant_id"])
    op.create_index("ix_criteria_mappings_criterion_id", "criteria_mappings", ["criterion_id"])

    op.create_table(
        "standard_entitlements",
        sa.Column("entitlement_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("standard_code", sa.String(length=64), nullable=False),
        sa.Column("licence_ref", sa.String(length=255), nullable=False),
        sa.Column("granted_by", sa.String(length=128), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "standard_code", name="uq_entitlement_tenant_standard"
        ),
    )
    op.create_index("ix_standard_entitlements_tenant_id", "standard_entitlements", ["tenant_id"])

    op.create_table(
        "audit_pack_registrations",
        sa.Column("registration_id", sa.String(length=64), primary_key=True),
        sa.Column("pack_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="registered"),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("release_key_id", sa.String(length=128), nullable=True),
        sa.Column("standard_code", sa.String(length=64), nullable=False),
        sa.Column("standard_version", sa.String(length=32), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("compatibility_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("registered_by", sa.String(length=128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("superseded_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pack_id", "version", name="uq_pack_registration_version"),
    )
    op.create_index(
        "ix_audit_pack_registrations_pack_id", "audit_pack_registrations", ["pack_id"]
    )

    # One compilation per engagement, enforced by the database. Recompiling would
    # replace the methodology an engagement is already running under, and the
    # service refusing is a weaker guarantee than the schema refusing.
    op.create_table(
        "pack_compilations",
        sa.Column("compilation_id", sa.String(length=64), primary_key=True),
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
        sa.Column(
            "registration_id",
            sa.String(length=64),
            sa.ForeignKey("audit_pack_registrations.registration_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("pack_id", sa.String(length=128), nullable=False),
        sa.Column("pack_version", sa.String(length=32), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.String(length=64), nullable=False),
        sa.Column("pins_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("pins_digest", sa.String(length=64), nullable=False),
        sa.Column("organization_context_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("gate_count", sa.Integer(), nullable=False),
        sa.Column("compiled_by", sa.String(length=128), nullable=False),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("engagement_id", name="uq_pack_compilation_engagement"),
        sa.CheckConstraint("task_count > 0", name="task_count_positive"),
    )
    op.create_index("ix_pack_compilations_tenant_id", "pack_compilations", ["tenant_id"])
    op.create_index("ix_pack_compilations_engagement_id", "pack_compilations", ["engagement_id"])


def downgrade() -> None:
    op.drop_index("ix_pack_compilations_engagement_id", table_name="pack_compilations")
    op.drop_index("ix_pack_compilations_tenant_id", table_name="pack_compilations")
    op.drop_table("pack_compilations")
    op.drop_index("ix_audit_pack_registrations_pack_id", table_name="audit_pack_registrations")
    op.drop_table("audit_pack_registrations")
    op.drop_index("ix_standard_entitlements_tenant_id", table_name="standard_entitlements")
    op.drop_table("standard_entitlements")
    op.drop_index("ix_criteria_mappings_criterion_id", table_name="criteria_mappings")
    op.drop_index("ix_criteria_mappings_tenant_id", table_name="criteria_mappings")
    op.drop_table("criteria_mappings")
    op.drop_index("ix_criteria_crosswalks_target", table_name="criteria_crosswalks")
    op.drop_index("ix_criteria_crosswalks_source", table_name="criteria_crosswalks")
    op.drop_table("criteria_crosswalks")
    op.drop_index("ix_criteria_standard_id", table_name="criteria")
    op.drop_table("criteria")
    op.drop_index("ix_standards_tenant_id", table_name="standards")
    op.drop_table("standards")
