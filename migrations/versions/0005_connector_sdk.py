"""Add connector instances, collection grants, runs, checkpoints, and source-object lineage.

Revision ID: 0005_connector_sdk
Revises: 0004_evidence_vault
Create Date: 2026-08-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_connector_sdk"
down_revision: Union[str, None] = "0004_evidence_vault"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connector_instances",
        sa.Column("connector_instance_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("connector_key", sa.String(length=128), nullable=False),
        sa.Column("connector_type", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("credential_ref", sa.String(length=512), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("last_health_status", sa.String(length=32), nullable=True),
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'disabled', 'error')",
            name="ck_connector_instances_valid_connector_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("connector_instance_id"),
        sa.UniqueConstraint("tenant_id", "connector_key", name="uq_connector_instance_key"),
    )
    op.create_index(
        "ix_connector_instances_tenant_id", "connector_instances", ["tenant_id"]
    )

    op.create_table(
        "collection_grants",
        sa.Column("grant_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("connector_instance_id", sa.String(length=64), nullable=False),
        sa.Column("grant_key", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("allowed_streams_json", sa.JSON(), nullable=False),
        sa.Column("resource_selectors_json", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("read_only", name="ck_collection_grants_collection_grant_read_only"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'revoked', 'expired')",
            name="ck_collection_grants_valid_collection_grant_status",
        ),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.connector_instance_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("grant_id"),
        sa.UniqueConstraint("tenant_id", "grant_key", name="uq_collection_grant_key"),
    )
    op.create_index(
        "ix_collection_grant_active",
        "collection_grants",
        ["tenant_id", "connector_instance_id", "status"],
    )
    op.create_index(
        "ix_collection_grants_connector_instance_id",
        "collection_grants",
        ["connector_instance_id"],
    )
    op.create_index("ix_collection_grants_tenant_id", "collection_grants", ["tenant_id"])

    op.create_table(
        "connector_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("connector_instance_id", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("cursor_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_connector_checkpoints_positive_checkpoint_version"),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.connector_instance_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint(
            "tenant_id", "connector_instance_id", "stream", name="uq_connector_checkpoint"
        ),
    )
    op.create_index(
        "ix_connector_checkpoints_connector_instance_id",
        "connector_checkpoints",
        ["connector_instance_id"],
    )
    op.create_index(
        "ix_connector_checkpoints_tenant_id", "connector_checkpoints", ["tenant_id"]
    )

    op.create_table(
        "connector_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("connector_instance_id", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_before_json", sa.JSON(), nullable=False),
        sa.Column("checkpoint_after_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("objects_seen", sa.Integer(), nullable=False),
        sa.Column("objects_ingested", sa.Integer(), nullable=False),
        sa.Column("objects_unchanged", sa.Integer(), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("schema_drift", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("objects_ingested >= 0", name="ck_connector_runs_nonnegative_objects_ingested"),
        sa.CheckConstraint("objects_seen >= 0", name="ck_connector_runs_nonnegative_objects_seen"),
        sa.CheckConstraint("objects_unchanged >= 0", name="ck_connector_runs_nonnegative_objects_unchanged"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_connector_runs_valid_connector_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.connector_instance_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["grant_id"], ["collection_grants.grant_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_connector_run_idempotency"),
    )
    op.create_index(
        "ix_connector_run_recent",
        "connector_runs",
        ["tenant_id", "connector_instance_id", "started_at"],
    )
    op.create_index(
        "ix_connector_runs_connector_instance_id",
        "connector_runs",
        ["connector_instance_id"],
    )
    op.create_index("ix_connector_runs_grant_id", "connector_runs", ["grant_id"])
    op.create_index("ix_connector_runs_tenant_id", "connector_runs", ["tenant_id"])

    op.create_table(
        "collected_source_objects",
        sa.Column("collected_object_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("connector_instance_id", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=128), nullable=False),
        sa.Column("source_object_id", sa.String(length=512), nullable=False),
        sa.Column("source_version", sa.String(length=512), nullable=False),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_instance_id"],
            ["connector_instances.connector_instance_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_records.evidence_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["connector_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("collected_object_id"),
        sa.UniqueConstraint(
            "run_id", "source_object_id", "source_version", name="uq_run_source_object"
        ),
    )
    op.create_index(
        "ix_collected_source_latest",
        "collected_source_objects",
        ["tenant_id", "connector_instance_id", "stream", "source_object_id"],
    )
    op.create_index(
        "ix_collected_source_objects_evidence_id",
        "collected_source_objects",
        ["evidence_id"],
    )
    op.create_index(
        "ix_collected_source_objects_run_id", "collected_source_objects", ["run_id"]
    )
    op.create_index(
        "ix_collected_source_objects_tenant_id", "collected_source_objects", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_collected_source_objects_tenant_id", table_name="collected_source_objects")
    op.drop_index("ix_collected_source_objects_run_id", table_name="collected_source_objects")
    op.drop_index("ix_collected_source_objects_evidence_id", table_name="collected_source_objects")
    op.drop_index("ix_collected_source_latest", table_name="collected_source_objects")
    op.drop_table("collected_source_objects")
    op.drop_index("ix_connector_runs_tenant_id", table_name="connector_runs")
    op.drop_index("ix_connector_runs_grant_id", table_name="connector_runs")
    op.drop_index("ix_connector_runs_connector_instance_id", table_name="connector_runs")
    op.drop_index("ix_connector_run_recent", table_name="connector_runs")
    op.drop_table("connector_runs")
    op.drop_index("ix_connector_checkpoints_tenant_id", table_name="connector_checkpoints")
    op.drop_index("ix_connector_checkpoints_connector_instance_id", table_name="connector_checkpoints")
    op.drop_table("connector_checkpoints")
    op.drop_index("ix_collection_grants_tenant_id", table_name="collection_grants")
    op.drop_index("ix_collection_grants_connector_instance_id", table_name="collection_grants")
    op.drop_index("ix_collection_grant_active", table_name="collection_grants")
    op.drop_table("collection_grants")
    op.drop_index("ix_connector_instances_tenant_id", table_name="connector_instances")
    op.drop_table("connector_instances")
