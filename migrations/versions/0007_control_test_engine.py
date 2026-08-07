"""Deterministic control-test registry and execution history.

Revision ID: 0007_control_test_engine
Revises: 0006_production_hardening
Create Date: 2026-08-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_control_test_engine"
down_revision: Union[str, None] = "0006_production_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "control_test_releases",
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("test_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=False),
        sa.Column("entrypoint", sa.String(length=255), nullable=False),
        sa.Column("package_path", sa.String(length=1024), nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("output_schema_json", sa.JSON(), nullable=False),
        sa.Column("parameter_schema_json", sa.JSON(), nullable=False),
        sa.Column("dataset_contracts_json", sa.JSON(), nullable=False),
        sa.Column("reconciliation_policy_json", sa.JSON(), nullable=False),
        sa.Column("sampling_policy_json", sa.JSON(), nullable=False),
        sa.Column("resource_limits_json", sa.JSON(), nullable=False),
        sa.Column("allowed_libraries_json", sa.JSON(), nullable=False),
        sa.Column("release_status", sa.String(length=16), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(length=128), nullable=True),
        sa.Column("signature_key_id", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("engine IN ('python', 'sql')", name="ck_control_test_releases_control_test_release_engine"),
        sa.CheckConstraint("release_status IN ('draft', 'released', 'retired')", name="ck_control_test_releases_control_test_release_status"),
        sa.PrimaryKeyConstraint("release_id"),
        sa.UniqueConstraint("test_id", "version", name="uq_control_test_release_version"),
    )
    op.create_index("ix_control_test_releases_test_id", "control_test_releases", ["test_id"])
    op.create_index("ix_control_test_releases_domain", "control_test_releases", ["domain"])

    op.create_table(
        "control_test_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("engagement_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("test_id", sa.String(length=128), nullable=False),
        sa.Column("test_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("population_count", sa.Integer(), nullable=True),
        sa.Column("sampled_count", sa.Integer(), nullable=True),
        sa.Column("reconciled_count", sa.Integer(), nullable=True),
        sa.Column("exception_count", sa.Integer(), nullable=True),
        sa.Column("population_complete", sa.Boolean(), nullable=True),
        sa.Column("conclusion", sa.String(length=64), nullable=True),
        sa.Column("input_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("execution_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("result_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("execution_environment_json", sa.JSON(), nullable=False),
        sa.Column("failure_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("period_end >= period_start", name="ck_control_test_runs_control_test_run_period_order"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'blocked', 'failed', 'cancelled')", name="ck_control_test_runs_control_test_run_status"),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.engagement_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["control_test_releases.release_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["engagement_tasks.task_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_control_test_run_idempotency"),
    )
    for column in ("tenant_id", "engagement_id", "task_id", "release_id", "test_id"):
        op.create_index(f"ix_control_test_runs_{column}", "control_test_runs", [column])
    op.create_index("ix_control_test_runs_tenant_engagement", "control_test_runs", ["tenant_id", "engagement_id", "created_at"])
    op.create_index("ix_control_test_runs_release_status", "control_test_runs", ["release_id", "status", "created_at"])

    op.create_table(
        "control_test_dataset_bindings",
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_name", sa.String(length=128), nullable=False),
        sa.Column("dataset_role", sa.String(length=32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("sampled_row_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("authoritative", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("dataset_role IN ('population', 'reference', 'exceptions')", name="ck_control_test_dataset_bindings_control_test_dataset_role"),
        sa.ForeignKeyConstraint(["run_id"], ["control_test_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("binding_id"),
        sa.UniqueConstraint("run_id", "dataset_name", name="uq_control_test_run_dataset"),
    )
    op.create_index("ix_control_test_dataset_bindings_tenant_id", "control_test_dataset_bindings", ["tenant_id"])
    op.create_index("ix_control_test_dataset_bindings_run_id", "control_test_dataset_bindings", ["run_id"])

    op.create_table(
        "control_test_exceptions",
        sa.Column("exception_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("exception_key", sa.String(length=255), nullable=False),
        sa.Column("subject_ref", sa.String(length=512), nullable=False),
        sa.Column("classification", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'approved_exception', 'false_positive', 'resolved')", name="ck_control_test_exceptions_control_test_exception_status"),
        sa.ForeignKeyConstraint(["run_id"], ["control_test_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("exception_id"),
        sa.UniqueConstraint("run_id", "exception_key", name="uq_control_test_run_exception"),
    )
    op.create_index("ix_control_test_exceptions_tenant_id", "control_test_exceptions", ["tenant_id"])
    op.create_index("ix_control_test_exceptions_run_id", "control_test_exceptions", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_control_test_exceptions_run_id", table_name="control_test_exceptions")
    op.drop_index("ix_control_test_exceptions_tenant_id", table_name="control_test_exceptions")
    op.drop_table("control_test_exceptions")
    op.drop_index("ix_control_test_dataset_bindings_run_id", table_name="control_test_dataset_bindings")
    op.drop_index("ix_control_test_dataset_bindings_tenant_id", table_name="control_test_dataset_bindings")
    op.drop_table("control_test_dataset_bindings")
    op.drop_index("ix_control_test_runs_release_status", table_name="control_test_runs")
    op.drop_index("ix_control_test_runs_tenant_engagement", table_name="control_test_runs")
    for column in ("test_id", "release_id", "task_id", "engagement_id", "tenant_id"):
        op.drop_index(f"ix_control_test_runs_{column}", table_name="control_test_runs")
    op.drop_table("control_test_runs")
    op.drop_index("ix_control_test_releases_domain", table_name="control_test_releases")
    op.drop_index("ix_control_test_releases_test_id", table_name="control_test_releases")
    op.drop_table("control_test_releases")
