from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from assuranceos.db.models import EvidenceRecord, EvidenceTransformation
from assuranceos.db.repositories import (
    AuditEventRepository,
    EngagementRepository,
    OutboxRepository,
    TenantRepository,
    new_id,
)
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from .custody import custody_event_hash
from .definitions import (
    CustodyEventItem,
    CustodyVerification,
    EvidenceItem,
    ExportVerification,
    GarbageCollectionReport,
    IntegrityReport,
    LineageEdge,
    LineageGraph,
)
from .exceptions import (
    AcquisitionConflictError,
    EvidenceDeletedError,
    EvidenceNotFoundError,
    ImmutableObjectConflictError,
    ObjectNotFoundError,
    RetentionPolicyError,
)
from .export import verify_export_package, write_export_package
from .inspection import ContentInspectionRejected, ContentInspector
from .signing import ManifestSigner
from .repository import VaultRepository
from .storage import LocalObjectStore, ObjectStore, sha256_bytes


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _evidence_item(record: EvidenceRecord) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=record.evidence_id,
        tenant_id=record.tenant_id,
        engagement_id=record.engagement_id,
        task_id=record.task_id,
        acquisition_key=record.acquisition_key,
        record_kind=record.record_kind,  # type: ignore[arg-type]
        source_type=record.source_type,
        source_locator=record.source_locator,
        content_sha256=record.content_sha256,
        storage_provider=record.storage_provider,
        storage_key=record.storage_key,
        object_uri=record.object_uri,
        original_filename=record.original_filename,
        mime_type=record.mime_type,
        content_encoding=record.content_encoding,
        size_bytes=record.size_bytes,
        classification=record.classification,
        source_time=record.source_time,
        collected_at=record.collected_at,
        accepted=record.accepted,
        tainted=record.tainted,
        integrity_status=record.integrity_status,  # type: ignore[arg-type]
        last_verified_at=record.last_verified_at,
        retention_until=record.retention_until,
        legal_hold=record.legal_hold,
        deleted_at=record.deleted_at,
        deletion_reason=record.deletion_reason,
        metadata=record.metadata_json,
    )


def _custody_item(event: Any) -> CustodyEventItem:
    return CustodyEventItem(
        custody_event_id=event.custody_event_id,
        evidence_id=event.evidence_id,
        sequence_no=event.sequence_no,
        action=event.action,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        occurred_at=event.occurred_at,
        details=event.details_json,
        previous_event_hash=event.previous_event_hash,
        event_hash=event.event_hash,
    )


class EvidenceVault:
    """Canonical evidence acquisition, custody, lineage, integrity, and export service."""

    def __init__(
        self,
        database: Database,
        object_store: ObjectStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        export_signer: ManifestSigner | None = None,
        inspector: ContentInspector | None = None,
    ):
        self.database = database
        self.object_store = object_store
        self.clock = clock
        self.export_signer = export_signer
        self.inspector = inspector

    @classmethod
    def local(
        cls,
        database: Database,
        root: Path,
        *,
        export_signer: ManifestSigner | None = None,
        inspector: ContentInspector | None = None,
    ) -> "EvidenceVault":
        return cls(
            database,
            LocalObjectStore(root),
            export_signer=export_signer,
            inspector=inspector,
        )

    def ingest_bytes(
        self,
        *,
        tenant_id: str,
        payload: bytes,
        source_type: str,
        source_locator: str,
        actor_id: str,
        actor_type: str = "user",
        engagement_id: str | None = None,
        task_id: str | None = None,
        acquisition_key: str | None = None,
        original_filename: str | None = None,
        mime_type: str | None = None,
        content_encoding: str | None = None,
        classification: str = "internal",
        source_time: datetime | None = None,
        accepted: bool = False,
        tainted: bool = False,
        retention_until: date | None = None,
        legal_hold: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        metadata = dict(metadata or {})
        if self.inspector is not None:
            inspection = self.inspector.inspect(
                payload=payload, mime_type=mime_type, filename=original_filename
            )
            if not inspection.accepted:
                raise ContentInspectionRejected(
                    f"evidence rejected by content inspection: {list(inspection.findings)}"
                )
            tainted = tainted or inspection.tainted
            metadata["content_inspection"] = {
                "findings": list(inspection.findings),
                **inspection.metadata,
            }
        digest = sha256_bytes(payload)
        with self.database.read_session() as session:
            if TenantRepository(session).get(tenant_id) is None:
                raise EvidenceNotFoundError(f"tenant not found: {tenant_id}")
            engagements = EngagementRepository(session)
            if engagement_id is not None and engagements.get(tenant_id, engagement_id) is None:
                raise EvidenceNotFoundError(f"engagement not found: {engagement_id}")
            if task_id is not None:
                task = engagements.get_task(tenant_id, task_id)
                if task is None:
                    raise EvidenceNotFoundError(f"task not found: {task_id}")
                if engagement_id is not None and task.engagement_id != engagement_id:
                    raise ValueError("task does not belong to the supplied engagement")
                if engagement_id is None:
                    engagement_id = task.engagement_id
        if acquisition_key:
            with self.database.read_session() as session:
                existing = VaultRepository(session).get_by_acquisition_key(
                    tenant_id, acquisition_key
                )
                if existing is not None:
                    if existing.content_sha256 != digest:
                        raise AcquisitionConflictError(
                            "acquisition key already identifies different evidence bytes"
                        )
                    return _evidence_item(existing)

        stored = self.object_store.put_bytes(
            tenant_id, payload, expected_sha256=digest
        )
        now = self.clock()
        evidence = EvidenceRecord(
            evidence_id=new_id("evd"),
            tenant_id=tenant_id,
            engagement_id=engagement_id,
            task_id=task_id,
            acquisition_key=acquisition_key,
            record_kind="original",
            source_type=source_type,
            source_locator=source_locator,
            content_sha256=digest,
            storage_provider=stored.provider,
            storage_key=stored.key,
            object_uri=stored.uri,
            original_filename=original_filename,
            mime_type=mime_type,
            content_encoding=content_encoding,
            size_bytes=stored.size_bytes,
            classification=classification,
            source_time=source_time,
            collected_at=now,
            accepted=accepted,
            tainted=tainted,
            integrity_status="verified",
            last_verified_at=now,
            retention_until=retention_until,
            legal_hold=legal_hold,
            metadata_json=metadata,
        )
        try:
            with self.database.transaction() as session:
                repository = VaultRepository(session)
                if acquisition_key:
                    existing = repository.get_by_acquisition_key(tenant_id, acquisition_key)
                    if existing is not None:
                        if existing.content_sha256 != digest:
                            raise AcquisitionConflictError(
                                "acquisition key already identifies different evidence bytes"
                            )
                        return _evidence_item(existing)
                repository.add_evidence(evidence)
                custody = repository.append_custody_event(
                    tenant_id=tenant_id,
                    evidence_id=evidence.evidence_id,
                    action="acquired",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    occurred_at=now,
                    details={
                        "source_type": source_type,
                        "source_locator": source_locator,
                        "sha256": digest,
                        "size_bytes": stored.size_bytes,
                        "object_created": stored.created,
                    },
                )
                self._append_domain_event(
                    session=session,
                    evidence=evidence,
                    event_type="evidence.acquired",
                    payload={
                        "evidence_id": evidence.evidence_id,
                        "record_kind": "original",
                        "content_sha256": digest,
                        "custody_head": custody.event_hash,
                    },
                )
        except IntegrityError as exc:
            if acquisition_key:
                with self.database.read_session() as session:
                    existing = VaultRepository(session).get_by_acquisition_key(
                        tenant_id, acquisition_key
                    )
                    if existing is not None and existing.content_sha256 == digest:
                        return _evidence_item(existing)
            raise AcquisitionConflictError(
                "evidence acquisition conflicts with canonical state"
            ) from exc
        return _evidence_item(evidence)

    def ingest_file(self, path: Path, **kwargs: Any) -> EvidenceItem:
        if "original_filename" not in kwargs:
            kwargs["original_filename"] = path.name
        return self.ingest_bytes(payload=path.read_bytes(), **kwargs)

    def create_derivative(
        self,
        *,
        tenant_id: str,
        source_evidence_ids: list[str],
        payload: bytes,
        operation: str,
        tool_version: str,
        actor_id: str,
        actor_type: str = "service",
        parameters: dict[str, Any] | None = None,
        source_locator: str | None = None,
        acquisition_key: str | None = None,
        original_filename: str | None = None,
        mime_type: str | None = None,
        content_encoding: str | None = None,
        classification: str | None = None,
        accepted: bool = False,
        tainted: bool | None = None,
        retention_until: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        unique_sources = list(dict.fromkeys(source_evidence_ids))
        if not unique_sources:
            raise ValueError("a derivative requires at least one source evidence record")
        with self.database.read_session() as session:
            repository = VaultRepository(session)
            sources = []
            for source_id in unique_sources:
                source = repository.get(tenant_id, source_id)
                if source is None:
                    raise EvidenceNotFoundError(f"source evidence not found: {source_id}")
                sources.append(source)
        source_classifications = {source.classification for source in sources}
        if classification is None:
            if len(source_classifications) != 1:
                raise ValueError(
                    "classification must be supplied when derivative sources have "
                    "different classifications"
                )
            inherited_classification = next(iter(source_classifications))
        else:
            inherited_classification = classification
        inherited_taint = any(source.tainted for source in sources) if tainted is None else tainted
        source_engagements = {source.engagement_id for source in sources}
        source_tasks = {source.task_id for source in sources}
        derived_engagement_id = (
            next(iter(source_engagements)) if len(source_engagements) == 1 else None
        )
        derived_task_id = next(iter(source_tasks)) if len(source_tasks) == 1 else None
        digest = sha256_bytes(payload)
        if acquisition_key:
            with self.database.read_session() as session:
                existing = VaultRepository(session).get_by_acquisition_key(
                    tenant_id, acquisition_key
                )
                if existing is not None:
                    if existing.content_sha256 != digest:
                        raise AcquisitionConflictError(
                            "acquisition key already identifies different derivative bytes"
                        )
                    return _evidence_item(existing)
        stored = self.object_store.put_bytes(tenant_id, payload, expected_sha256=digest)
        now = self.clock()
        derivative = EvidenceRecord(
            evidence_id=new_id("evd"),
            tenant_id=tenant_id,
            engagement_id=derived_engagement_id,
            task_id=derived_task_id,
            acquisition_key=acquisition_key,
            record_kind="derived",
            source_type="derived",
            source_locator=source_locator
            or f"derived:{operation}:{','.join(sorted(unique_sources))}",
            content_sha256=digest,
            storage_provider=stored.provider,
            storage_key=stored.key,
            object_uri=stored.uri,
            original_filename=original_filename,
            mime_type=mime_type,
            content_encoding=content_encoding,
            size_bytes=stored.size_bytes,
            classification=inherited_classification,
            collected_at=now,
            accepted=accepted,
            tainted=inherited_taint,
            integrity_status="verified",
            last_verified_at=now,
            retention_until=retention_until,
            legal_hold=any(source.legal_hold for source in sources),
            metadata_json=metadata or {},
        )
        try:
            with self.database.transaction() as session:
                repository = VaultRepository(session)
                if acquisition_key:
                    existing = repository.get_by_acquisition_key(tenant_id, acquisition_key)
                    if existing is not None:
                        if existing.content_sha256 != digest:
                            raise AcquisitionConflictError(
                                "acquisition key already identifies different derivative bytes"
                            )
                        return _evidence_item(existing)
                current_sources = []
                for source_id in unique_sources:
                    source = repository.get(tenant_id, source_id, for_update=True)
                    if source is None:
                        raise EvidenceNotFoundError(f"source evidence not found: {source_id}")
                    current_sources.append(source)
                repository.add_evidence(derivative)
                for source in current_sources:
                    transformation = EvidenceTransformation(
                        transformation_id=new_id("trn"),
                        tenant_id=tenant_id,
                        source_evidence_id=source.evidence_id,
                        derived_evidence_id=derivative.evidence_id,
                        operation=operation,
                        tool_version=tool_version,
                        parameters_json=parameters or {},
                        created_at=now,
                    )
                    repository.add_transformation(transformation)
                    repository.append_custody_event(
                        tenant_id=tenant_id,
                        evidence_id=source.evidence_id,
                        action="used_for_derivation",
                        actor_type=actor_type,
                        actor_id=actor_id,
                        occurred_at=now,
                        details={
                            "derived_evidence_id": derivative.evidence_id,
                            "operation": operation,
                            "tool_version": tool_version,
                        },
                    )
                custody = repository.append_custody_event(
                    tenant_id=tenant_id,
                    evidence_id=derivative.evidence_id,
                    action="derived",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    occurred_at=now,
                    details={
                        "source_evidence_ids": unique_sources,
                        "operation": operation,
                        "tool_version": tool_version,
                        "sha256": digest,
                    },
                )
                self._append_domain_event(
                    session=session,
                    evidence=derivative,
                    event_type="evidence.derived",
                    payload={
                        "evidence_id": derivative.evidence_id,
                        "source_evidence_ids": unique_sources,
                        "operation": operation,
                        "custody_head": custody.event_hash,
                    },
                )
        except IntegrityError as exc:
            if acquisition_key:
                with self.database.read_session() as session:
                    existing = VaultRepository(session).get_by_acquisition_key(
                        tenant_id, acquisition_key
                    )
                    if existing is not None and existing.content_sha256 == digest:
                        return _evidence_item(existing)
            raise AcquisitionConflictError(
                "derivative acquisition conflicts with canonical state"
            ) from exc
        return _evidence_item(derivative)

    def get(
        self, tenant_id: str, evidence_id: str, *, include_deleted: bool = False
    ) -> EvidenceItem:
        with self.database.read_session() as session:
            record = VaultRepository(session).get(
                tenant_id, evidence_id, include_deleted=include_deleted
            )
            if record is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            return _evidence_item(record)

    def list(
        self,
        tenant_id: str,
        *,
        engagement_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 500,
    ) -> list[EvidenceItem]:
        with self.database.read_session() as session:
            records = VaultRepository(session).list_records(
                tenant_id,
                engagement_id=engagement_id,
                include_deleted=include_deleted,
                limit=limit,
            )
            return [_evidence_item(record) for record in records]

    def read_bytes(
        self,
        tenant_id: str,
        evidence_id: str,
        *,
        actor_id: str,
        actor_type: str = "user",
        purpose: str,
    ) -> bytes:
        record = self._get_active_record(tenant_id, evidence_id)
        if record.storage_key is None:
            raise ObjectNotFoundError(f"evidence has no stored object: {evidence_id}")
        with self.object_store.open(tenant_id, record.storage_key) as handle:
            payload = handle.read()
        now = self.clock()
        with self.database.transaction() as session:
            repository = VaultRepository(session)
            repository.append_custody_event(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                action="accessed",
                actor_type=actor_type,
                actor_id=actor_id,
                occurred_at=now,
                details={"purpose": purpose, "bytes_read": len(payload)},
            )
        return payload

    def verify_integrity(
        self,
        tenant_id: str,
        evidence_id: str,
        *,
        actor_id: str = "evidence-vault",
    ) -> IntegrityReport:
        record = self._get_active_record(tenant_id, evidence_id)
        if record.storage_key is None:
            raise ObjectNotFoundError(f"evidence has no stored object: {evidence_id}")
        now = self.clock()
        actual_sha256: str | None = None
        actual_size: int | None = None
        status = "verified"
        failure: Exception | None = None
        try:
            stored = self.object_store.verify(
                tenant_id,
                record.storage_key,
                expected_sha256=record.content_sha256,
                expected_size=record.size_bytes,
            )
            actual_sha256 = stored.sha256
            actual_size = stored.size_bytes
        except ObjectNotFoundError as exc:
            status = "missing"
            failure = exc
        except ImmutableObjectConflictError as exc:
            status = "mismatch"
            failure = exc
            try:
                stored = self.object_store.stat(tenant_id, record.storage_key)
                actual_sha256 = stored.sha256
                actual_size = stored.size_bytes
            except ObjectNotFoundError:
                status = "missing"
        with self.database.transaction() as session:
            repository = VaultRepository(session)
            current = repository.get(tenant_id, evidence_id, for_update=True)
            if current is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            current.integrity_status = status
            current.last_verified_at = now
            repository.append_custody_event(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                action="integrity_verified" if status == "verified" else "integrity_failed",
                actor_type="service",
                actor_id=actor_id,
                occurred_at=now,
                details={
                    "status": status,
                    "expected_sha256": record.content_sha256,
                    "actual_sha256": actual_sha256,
                    "expected_size": record.size_bytes,
                    "actual_size": actual_size,
                },
            )
        report = IntegrityReport(
            evidence_id=evidence_id,
            status=status,  # type: ignore[arg-type]
            expected_sha256=record.content_sha256,
            actual_sha256=actual_sha256,
            expected_size=record.size_bytes,
            actual_size=actual_size,
            verified_at=now,
        )
        if failure is not None:
            # The report remains available through canonical state; callers still receive a clear
            # failure rather than silently treating corrupted evidence as usable.
            failure.add_note(report.model_dump_json())
            raise failure
        return report

    def list_custody(self, tenant_id: str, evidence_id: str) -> list[CustodyEventItem]:
        with self.database.read_session() as session:
            repository = VaultRepository(session)
            if repository.get(tenant_id, evidence_id, include_deleted=True) is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            return [
                _custody_item(event)
                for event in repository.list_custody_events(tenant_id, evidence_id)
            ]

    def verify_custody_chain(
        self, tenant_id: str, evidence_id: str
    ) -> CustodyVerification:
        events = self.list_custody(tenant_id, evidence_id)
        previous_hash: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence_no != expected_sequence:
                return CustodyVerification(
                    evidence_id=evidence_id,
                    valid=False,
                    event_count=len(events),
                    head_hash=events[-1].event_hash if events else None,
                    error=(
                        f"expected custody sequence {expected_sequence}, "
                        f"found {event.sequence_no}"
                    ),
                )
            expected_hash = custody_event_hash(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                sequence_no=event.sequence_no,
                action=event.action,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                occurred_at=event.occurred_at,
                details=event.details,
                previous_event_hash=previous_hash,
            )
            if event.previous_event_hash != previous_hash or event.event_hash != expected_hash:
                return CustodyVerification(
                    evidence_id=evidence_id,
                    valid=False,
                    event_count=len(events),
                    head_hash=events[-1].event_hash if events else None,
                    error=f"custody hash mismatch at sequence {event.sequence_no}",
                )
            previous_hash = event.event_hash
        return CustodyVerification(
            evidence_id=evidence_id,
            valid=True,
            event_count=len(events),
            head_hash=previous_hash,
        )

    def lineage(self, tenant_id: str, evidence_id: str) -> LineageGraph:
        with self.database.read_session() as session:
            repository = VaultRepository(session)
            root = repository.get(tenant_id, evidence_id, include_deleted=True)
            if root is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            all_edges = repository.all_transformations(tenant_id)
            connected = {evidence_id}
            changed = True
            while changed:
                changed = False
                for edge in all_edges:
                    if (
                        edge.source_evidence_id in connected
                        or edge.derived_evidence_id in connected
                    ):
                        before = len(connected)
                        connected.add(edge.source_evidence_id)
                        connected.add(edge.derived_evidence_id)
                        changed = changed or len(connected) != before
            records = [
                repository.get(tenant_id, item_id, include_deleted=True)
                for item_id in sorted(connected)
            ]
            edges = [
                edge
                for edge in all_edges
                if edge.source_evidence_id in connected
                and edge.derived_evidence_id in connected
            ]
        return LineageGraph(
            root_evidence_id=evidence_id,
            nodes=[_evidence_item(record) for record in records if record is not None],
            edges=[
                LineageEdge(
                    transformation_id=edge.transformation_id,
                    source_evidence_id=edge.source_evidence_id,
                    derived_evidence_id=edge.derived_evidence_id,
                    operation=edge.operation,
                    tool_version=edge.tool_version,
                    parameters=edge.parameters_json,
                    created_at=edge.created_at,
                )
                for edge in edges
            ],
        )

    def set_retention(
        self,
        tenant_id: str,
        evidence_id: str,
        *,
        actor_id: str,
        retention_until: date | None,
        legal_hold: bool,
        reason: str,
    ) -> EvidenceItem:
        now = self.clock()
        with self.database.transaction() as session:
            repository = VaultRepository(session)
            record = repository.get(tenant_id, evidence_id, for_update=True)
            if record is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            record.retention_until = retention_until
            record.legal_hold = legal_hold
            repository.append_custody_event(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                action="retention_updated",
                actor_type="user",
                actor_id=actor_id,
                occurred_at=now,
                details={
                    "retention_until": _iso(retention_until),
                    "legal_hold": legal_hold,
                    "reason": reason,
                },
            )
            return _evidence_item(record)

    def purge(
        self,
        tenant_id: str,
        evidence_id: str,
        *,
        actor_id: str,
        reason: str,
        as_of: date | None = None,
    ) -> EvidenceItem:
        effective_date = as_of or self.clock().date()
        now = self.clock()
        with self.database.transaction() as session:
            repository = VaultRepository(session)
            record = repository.get(
                tenant_id, evidence_id, include_deleted=True, for_update=True
            )
            if record is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            if record.deleted_at is not None:
                return _evidence_item(record)
            if record.legal_hold:
                raise RetentionPolicyError("evidence is under legal hold")
            if record.retention_until is None:
                raise RetentionPolicyError("evidence has no approved retention expiry")
            if record.retention_until > effective_date:
                raise RetentionPolicyError(
                    f"evidence is retained until {record.retention_until.isoformat()}"
                )
            record.deleted_at = now
            record.deletion_reason = reason
            record.integrity_status = "purged"
            repository.append_custody_event(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                action="tombstoned",
                actor_type="user",
                actor_id=actor_id,
                occurred_at=now,
                details={"reason": reason, "effective_date": effective_date.isoformat()},
            )
            self._append_domain_event(
                session=session,
                evidence=record,
                event_type="evidence.tombstoned",
                payload={"evidence_id": evidence_id, "reason": reason},
            )
            return _evidence_item(record)

    def collect_garbage(
        self,
        tenant_id: str,
        *,
        grace_period: timedelta = timedelta(hours=24),
    ) -> GarbageCollectionReport:
        cutoff = self.clock() - grace_period
        with self.database.read_session() as session:
            active_keys = VaultRepository(session).active_storage_keys(tenant_id)
        objects = self.object_store.iter_objects(tenant_id)
        deleted_keys: list[str] = []
        retained = 0
        for stored in objects:
            if stored.key in active_keys or stored.modified_at > cutoff:
                retained += 1
                continue
            if self.object_store.delete(tenant_id, stored.key):
                deleted_keys.append(stored.key)
        return GarbageCollectionReport(
            tenant_id=tenant_id,
            examined=len(objects),
            deleted=len(deleted_keys),
            retained=retained,
            deleted_keys=deleted_keys,
        )

    def create_export(
        self,
        *,
        tenant_id: str,
        evidence_ids: list[str],
        destination: Path,
        actor_id: str,
        purpose: str,
        include_ancestors: bool = True,
    ) -> ExportVerification:
        requested = set(evidence_ids)
        if not requested:
            raise ValueError("an export requires at least one evidence record")
        with self.database.read_session() as session:
            repository = VaultRepository(session)
            all_edges = repository.all_transformations(tenant_id)
            selected = set(requested)
            if include_ancestors:
                changed = True
                while changed:
                    changed = False
                    for edge in all_edges:
                        if (
                            edge.derived_evidence_id in selected
                            and edge.source_evidence_id not in selected
                        ):
                            selected.add(edge.source_evidence_id)
                            changed = True
            records: list[EvidenceRecord] = []
            for evidence_id in sorted(selected):
                record = repository.get(tenant_id, evidence_id)
                if record is None:
                    raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
                if record.storage_key is None:
                    raise ObjectNotFoundError(f"evidence has no stored object: {evidence_id}")
                records.append(record)
            edges = [
                edge
                for edge in all_edges
                if edge.source_evidence_id in selected
                and edge.derived_evidence_id in selected
            ]
            custody_manifests = {
                record.evidence_id: [
                    {
                        "sequence_no": event.sequence_no,
                        "action": event.action,
                        "actor_type": event.actor_type,
                        "actor_id": event.actor_id,
                        "occurred_at": _iso(event.occurred_at),
                        "details": event.details_json,
                        "previous_event_hash": event.previous_event_hash,
                        "event_hash": event.event_hash,
                    }
                    for event in repository.list_custody_events(
                        tenant_id, record.evidence_id
                    )
                ]
                for record in records
            }

        objects: dict[str, bytes] = {}
        for record in records:
            assert record.storage_key is not None
            self.object_store.verify(
                tenant_id,
                record.storage_key,
                expected_sha256=record.content_sha256,
                expected_size=record.size_bytes,
            )
            with self.object_store.open(tenant_id, record.storage_key) as handle:
                objects.setdefault(record.content_sha256, handle.read())

        export_id = new_id("exp")
        generated_at = self.clock()
        object_manifest = [
            {"sha256": digest, "size_bytes": len(payload)}
            for digest, payload in sorted(objects.items())
        ]
        evidence_manifest = [
            {
                "evidence_id": record.evidence_id,
                "record_kind": record.record_kind,
                "source_type": record.source_type,
                "source_locator": record.source_locator,
                "content_sha256": record.content_sha256,
                "size_bytes": record.size_bytes,
                "mime_type": record.mime_type,
                "classification": record.classification,
                "source_time": _iso(record.source_time),
                "collected_at": _iso(record.collected_at),
                "accepted": record.accepted,
                "tainted": record.tainted,
                "custody_head": (
                    custody_manifests[record.evidence_id][-1]["event_hash"]
                    if custody_manifests[record.evidence_id]
                    else None
                ),
                "custody": custody_manifests[record.evidence_id],
                "metadata": record.metadata_json,
            }
            for record in records
        ]
        lineage_manifest = [
            {
                "transformation_id": edge.transformation_id,
                "source_evidence_id": edge.source_evidence_id,
                "derived_evidence_id": edge.derived_evidence_id,
                "operation": edge.operation,
                "tool_version": edge.tool_version,
                "parameters": edge.parameters_json,
                "created_at": _iso(edge.created_at),
            }
            for edge in edges
        ]
        manifest = {
            "schema": (
                "assurance.evidence_export.v2"
                if self.export_signer is not None
                else "assurance.evidence_export.v1"
            ),
            "export_id": export_id,
            "tenant_id": tenant_id,
            "generated_at": _iso(generated_at),
            "purpose": purpose,
            "requested_evidence_ids": sorted(requested),
            "evidence": evidence_manifest,
            "lineage": lineage_manifest,
            "objects": object_manifest,
        }
        package_sha256, manifest_sha256 = write_export_package(
            destination,
            manifest=manifest,
            objects=objects,
            signer=self.export_signer,
        )
        verification = verify_export_package(destination)
        if not verification.valid:
            raise RuntimeError(f"new evidence export failed verification: {verification.errors}")
        now = self.clock()
        with self.database.transaction() as session:
            repository = VaultRepository(session)
            for record in records:
                repository.append_custody_event(
                    tenant_id=tenant_id,
                    evidence_id=record.evidence_id,
                    action="exported",
                    actor_type="user",
                    actor_id=actor_id,
                    occurred_at=now,
                    details={
                        "export_id": export_id,
                        "purpose": purpose,
                        "package_sha256": package_sha256,
                        "manifest_sha256": manifest_sha256,
                    },
                )
            OutboxRepository(session).add(
                tenant_id=tenant_id,
                aggregate_type="evidence_export",
                aggregate_id=export_id,
                event_type="evidence.export.created",
                payload={
                    "export_id": export_id,
                    "evidence_ids": sorted(selected),
                    "package_sha256": package_sha256,
                    "manifest_sha256": manifest_sha256,
                },
                idempotency_key=f"evidence.export.created:{export_id}",
            )
        return verification

    @staticmethod
    def verify_export(
        path: Path, *, trusted_public_keys: dict[str, bytes] | None = None
    ) -> ExportVerification:
        return verify_export_package(path, trusted_public_keys=trusted_public_keys)

    def _get_active_record(self, tenant_id: str, evidence_id: str) -> EvidenceRecord:
        with self.database.read_session() as session:
            repository = VaultRepository(session)
            record = repository.get(tenant_id, evidence_id, include_deleted=True)
            if record is None:
                raise EvidenceNotFoundError(f"evidence not found: {evidence_id}")
            if record.deleted_at is not None:
                raise EvidenceDeletedError(f"evidence was tombstoned: {evidence_id}")
            return record

    @staticmethod
    def _append_domain_event(
        *,
        session: Any,
        evidence: EvidenceRecord,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            tenant_id=evidence.tenant_id,
            engagement_id=evidence.engagement_id,
            task_id=evidence.task_id,
            payload=payload,
        )
        AuditEventRepository(session).append(event)
        OutboxRepository(session).add(
            tenant_id=evidence.tenant_id,
            aggregate_type="evidence",
            aggregate_id=evidence.evidence_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=f"{event_type}:{event.event_id}",
        )
