from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assuranceos.db.models import EvidenceCustodyEvent, EvidenceRecord, EvidenceTransformation
from assuranceos.db.repositories import new_id

from .custody import custody_event_hash


class VaultRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        self.session.add(evidence)
        self.session.flush()
        return evidence

    def get(
        self,
        tenant_id: str,
        evidence_id: str,
        *,
        include_deleted: bool = False,
        for_update: bool = False,
    ) -> EvidenceRecord | None:
        stmt = select(EvidenceRecord).where(
            EvidenceRecord.tenant_id == tenant_id,
            EvidenceRecord.evidence_id == evidence_id,
        )
        if not include_deleted:
            stmt = stmt.where(EvidenceRecord.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)

    def get_by_acquisition_key(
        self, tenant_id: str, acquisition_key: str
    ) -> EvidenceRecord | None:
        return self.session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.acquisition_key == acquisition_key,
            )
        )

    def list_records(
        self,
        tenant_id: str,
        *,
        engagement_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 500,
    ) -> list[EvidenceRecord]:
        stmt = select(EvidenceRecord).where(EvidenceRecord.tenant_id == tenant_id)
        if engagement_id is not None:
            stmt = stmt.where(EvidenceRecord.engagement_id == engagement_id)
        if not include_deleted:
            stmt = stmt.where(EvidenceRecord.deleted_at.is_(None))
        stmt = stmt.order_by(EvidenceRecord.collected_at, EvidenceRecord.evidence_id).limit(limit)
        return list(self.session.scalars(stmt))

    def active_storage_keys(self, tenant_id: str) -> set[str]:
        stmt = select(EvidenceRecord.storage_key).where(
            EvidenceRecord.tenant_id == tenant_id,
            EvidenceRecord.deleted_at.is_(None),
            EvidenceRecord.storage_key.is_not(None),
        )
        return {key for key in self.session.scalars(stmt) if key is not None}

    def add_transformation(self, transformation: EvidenceTransformation) -> EvidenceTransformation:
        self.session.add(transformation)
        self.session.flush()
        return transformation

    def transformations_touching(
        self, tenant_id: str, evidence_ids: Iterable[str]
    ) -> list[EvidenceTransformation]:
        ids = set(evidence_ids)
        if not ids:
            return []
        stmt = (
            select(EvidenceTransformation)
            .where(
                EvidenceTransformation.tenant_id == tenant_id,
                (
                    EvidenceTransformation.source_evidence_id.in_(ids)
                    | EvidenceTransformation.derived_evidence_id.in_(ids)
                ),
            )
            .order_by(EvidenceTransformation.created_at, EvidenceTransformation.transformation_id)
        )
        return list(self.session.scalars(stmt))

    def all_transformations(self, tenant_id: str) -> list[EvidenceTransformation]:
        stmt = (
            select(EvidenceTransformation)
            .where(EvidenceTransformation.tenant_id == tenant_id)
            .order_by(EvidenceTransformation.created_at, EvidenceTransformation.transformation_id)
        )
        return list(self.session.scalars(stmt))

    def append_custody_event(
        self,
        *,
        tenant_id: str,
        evidence_id: str,
        action: str,
        actor_type: str,
        actor_id: str,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> EvidenceCustodyEvent:
        # Locking the evidence row serializes custody sequence assignment on PostgreSQL. SQLite
        # serializes writes at the database level and safely ignores FOR UPDATE.
        evidence = self.get(
            tenant_id,
            evidence_id,
            include_deleted=True,
            for_update=True,
        )
        if evidence is None:
            raise LookupError(f"evidence not found: {evidence_id}")
        previous = self.session.scalar(
            select(EvidenceCustodyEvent)
            .where(
                EvidenceCustodyEvent.tenant_id == tenant_id,
                EvidenceCustodyEvent.evidence_id == evidence_id,
            )
            .order_by(EvidenceCustodyEvent.sequence_no.desc())
            .limit(1)
        )
        sequence_no = (previous.sequence_no if previous else 0) + 1
        previous_hash = previous.event_hash if previous else None
        event_details = details or {}
        event = EvidenceCustodyEvent(
            custody_event_id=new_id("cst"),
            tenant_id=tenant_id,
            evidence_id=evidence_id,
            sequence_no=sequence_no,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=occurred_at,
            details_json=event_details,
            previous_event_hash=previous_hash,
            event_hash=custody_event_hash(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                sequence_no=sequence_no,
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                occurred_at=occurred_at,
                details=event_details,
                previous_event_hash=previous_hash,
            ),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_custody_events(
        self, tenant_id: str, evidence_id: str
    ) -> list[EvidenceCustodyEvent]:
        stmt = (
            select(EvidenceCustodyEvent)
            .where(
                EvidenceCustodyEvent.tenant_id == tenant_id,
                EvidenceCustodyEvent.evidence_id == evidence_id,
            )
            .order_by(EvidenceCustodyEvent.sequence_no)
        )
        return list(self.session.scalars(stmt))

    def custody_head(self, tenant_id: str, evidence_id: str) -> str | None:
        return self.session.scalar(
            select(EvidenceCustodyEvent.event_hash)
            .where(
                EvidenceCustodyEvent.tenant_id == tenant_id,
                EvidenceCustodyEvent.evidence_id == evidence_id,
            )
            .order_by(EvidenceCustodyEvent.sequence_no.desc())
            .limit(1)
        )

    def count_active_references(self, tenant_id: str, storage_key: str) -> int:
        value = self.session.scalar(
            select(func.count(EvidenceRecord.evidence_id)).where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.storage_key == storage_key,
                EvidenceRecord.deleted_at.is_(None),
            )
        )
        return int(value or 0)
