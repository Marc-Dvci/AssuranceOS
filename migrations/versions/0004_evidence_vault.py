"""Add content-addressed evidence vault provenance and custody state.

Revision ID: 0004_evidence_vault
Revises: 0003_recurring_scheduler
Create Date: 2026-08-06
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_evidence_vault"
down_revision: Union[str, None] = "0003_recurring_scheduler"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _canonical_datetime(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_hash(
    *,
    tenant_id: str,
    evidence_id: str,
    occurred_at: datetime | str,
    details: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "action": "legacy_registered",
            "actor_id": "migration:0004_evidence_vault",
            "actor_type": "system",
            "details": details,
            "evidence_id": evidence_id,
            "occurred_at": _canonical_datetime(occurred_at),
            "previous_event_hash": None,
            "sequence_no": 1,
            "tenant_id": tenant_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table("evidence_records") as batch_op:
        batch_op.drop_constraint("uq_evidence_records_tenant_id", type_="unique")
        batch_op.add_column(sa.Column("acquisition_key", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "record_kind",
                sa.String(length=16),
                nullable=False,
                server_default="original",
            )
        )
        batch_op.add_column(
            sa.Column(
                "storage_provider",
                sa.String(length=32),
                nullable=False,
                server_default="legacy",
            )
        )
        batch_op.add_column(sa.Column("storage_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("original_filename", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("content_encoding", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "integrity_status",
                sa.String(length=16),
                nullable=False,
                server_default="unverified",
            )
        )
        batch_op.add_column(
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deletion_reason", sa.Text(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_evidence_acquisition_key", ["tenant_id", "acquisition_key"]
        )
        batch_op.create_check_constraint(
            "ck_evidence_records_valid_record_kind",
            "record_kind IN ('original', 'derived')",
        )
        batch_op.create_check_constraint(
            "ck_evidence_records_valid_integrity_status",
            "integrity_status IN ('unverified', 'verified', 'mismatch', 'missing', 'purged')",
        )
        batch_op.create_index(
            "ix_evidence_source_digest",
            ["tenant_id", "source_locator", "content_sha256"],
            unique=False,
        )
        batch_op.create_index(
            "ix_evidence_active_object",
            ["tenant_id", "storage_key", "deleted_at"],
            unique=False,
        )

    op.create_table(
        "evidence_custody_events",
        sa.Column("custody_event_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("previous_event_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("sequence_no > 0", name="ck_evidence_custody_events_positive_sequence"),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence_records.evidence_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("custody_event_id"),
        sa.UniqueConstraint(
            "tenant_id", "evidence_id", "sequence_no", name="uq_evidence_custody_sequence"
        ),
        sa.UniqueConstraint("tenant_id", "event_hash", name="uq_evidence_custody_hash"),
    )
    op.create_index(
        "ix_evidence_custody_events_tenant_id",
        "evidence_custody_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_evidence_custody_events_evidence_id",
        "evidence_custody_events",
        ["evidence_id"],
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT evidence_id, tenant_id, collected_at, content_sha256, source_type, "
            "source_locator FROM evidence_records ORDER BY tenant_id, evidence_id"
        )
    ).mappings()
    for row in rows:
        details = {
            "migration_revision": revision,
            "content_sha256": row["content_sha256"],
            "source_type": row["source_type"],
            "source_locator": row["source_locator"],
        }
        event_hash = _event_hash(
            tenant_id=row["tenant_id"],
            evidence_id=row["evidence_id"],
            occurred_at=row["collected_at"],
            details=details,
        )
        custody_seed = f"{row['tenant_id']}:{row['evidence_id']}".encode()
        custody_event_id = f"cst_{hashlib.sha256(custody_seed).hexdigest()[:20]}"
        connection.execute(
            sa.text(
                "INSERT INTO evidence_custody_events ("
                "custody_event_id, tenant_id, evidence_id, sequence_no, action, actor_type, "
                "actor_id, occurred_at, details_json, previous_event_hash, event_hash"
                ") VALUES ("
                ":custody_event_id, :tenant_id, :evidence_id, 1, 'legacy_registered', "
                "'system', 'migration:0004_evidence_vault', :occurred_at, :details_json, NULL, "
                ":event_hash)"
            ),
            {
                "custody_event_id": custody_event_id,
                "tenant_id": row["tenant_id"],
                "evidence_id": row["evidence_id"],
                "occurred_at": row["collected_at"],
                "details_json": json.dumps(details, sort_keys=True),
                "event_hash": event_hash,
            },
        )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_custody_events_evidence_id", table_name="evidence_custody_events"
    )
    op.drop_index(
        "ix_evidence_custody_events_tenant_id", table_name="evidence_custody_events"
    )
    op.drop_table("evidence_custody_events")

    with op.batch_alter_table("evidence_records") as batch_op:
        batch_op.drop_index("ix_evidence_active_object")
        batch_op.drop_index("ix_evidence_source_digest")
        batch_op.drop_constraint("ck_evidence_records_valid_integrity_status", type_="check")
        batch_op.drop_constraint("ck_evidence_records_valid_record_kind", type_="check")
        batch_op.drop_constraint("uq_evidence_acquisition_key", type_="unique")
        batch_op.drop_column("deletion_reason")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("last_verified_at")
        batch_op.drop_column("integrity_status")
        batch_op.drop_column("content_encoding")
        batch_op.drop_column("original_filename")
        batch_op.drop_column("storage_key")
        batch_op.drop_column("storage_provider")
        batch_op.drop_column("record_kind")
        batch_op.drop_column("acquisition_key")
        batch_op.create_unique_constraint(
            "uq_evidence_records_tenant_id",
            ["tenant_id", "source_locator", "content_sha256"],
        )
