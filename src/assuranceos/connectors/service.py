from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

from assuranceos.db.models import (
    CollectedSourceObject,
    CollectionGrant,
    ConnectorInstance,
    ConnectorRun,
)
from assuranceos.db.repositories import (
    AuditEventRepository,
    OutboxRepository,
    TenantRepository,
    new_id,
)
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent
from assuranceos.vault import EvidenceVault

from .definitions import (
    CollectionGrantInput,
    CollectionGrantView,
    CollectionRequest,
    ConnectorInstanceInput,
    ConnectorInstanceView,
    ConnectorRunSummary,
    json_sha256,
)
from .exceptions import (
    CollectionGrantError,
    CollectionGrantExpiredError,
    CollectionScopeError,
    ConnectorNotFoundError,
    SourceVersionConflictError,
)
from .protocol import Connector
from .repository import ConnectorRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _instance_view(row: ConnectorInstance) -> ConnectorInstanceView:
    return ConnectorInstanceView(
        connector_instance_id=row.connector_instance_id,
        tenant_id=row.tenant_id,
        connector_key=row.connector_key,
        connector_type=row.connector_type,
        display_name=row.display_name,
        base_url=row.base_url,
        status=row.status,
        credential_ref=row.credential_ref,
        config=row.config_json,
        last_health_status=row.last_health_status,
        last_health_checked_at=row.last_health_checked_at,
        last_health_details=row.last_health_details_json,
    )


def _grant_view(row: CollectionGrant) -> CollectionGrantView:
    return CollectionGrantView(
        grant_id=row.grant_id,
        tenant_id=row.tenant_id,
        connector_instance_id=row.connector_instance_id,
        grant_key=row.grant_key,
        purpose=row.purpose,
        status=row.status,
        read_only=row.read_only,
        allowed_streams=row.allowed_streams_json,
        resource_selectors=row.resource_selectors_json,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        expires_at=row.expires_at,
        revoked_by=row.revoked_by,
        revoked_at=row.revoked_at,
        revocation_reason=row.revocation_reason,
    )


def _run_summary(row: ConnectorRun) -> ConnectorRunSummary:
    return ConnectorRunSummary(
        run_id=row.run_id,
        tenant_id=row.tenant_id,
        connector_instance_id=row.connector_instance_id,
        grant_id=row.grant_id,
        stream=row.stream,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        checkpoint_before=row.checkpoint_before_json,
        checkpoint_after=row.checkpoint_after_json,
        objects_seen=row.objects_seen,
        objects_ingested=row.objects_ingested,
        objects_unchanged=row.objects_unchanged,
        schema_fingerprint=row.schema_fingerprint,
        schema_drift=row.schema_drift,
        last_error=row.last_error,
        metrics=row.metrics_json,
    )


def _selector_allows(allowed: Any, requested: Any) -> bool:
    if allowed == "*":
        return True
    if isinstance(allowed, list):
        requested_values = requested if isinstance(requested, list) else [requested]
        return all(value in allowed for value in requested_values)
    if isinstance(allowed, dict) and isinstance(requested, dict):
        return all(key in allowed and _selector_allows(allowed[key], value) for key, value in requested.items())
    return allowed == requested


def _schema_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _schema_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        shapes = {_canonical_shape_key(_schema_shape(item)) for item in value}
        return [sorted(shapes)]
    if value is None:
        return "null"
    return type(value).__name__


def _canonical_shape_key(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ConnectorService:
    """Registers connectors, enforces grants, and persists collection runs into the vault."""

    def __init__(
        self,
        database: Database,
        vault: EvidenceVault,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.database = database
        self.vault = vault
        self.clock = clock

    def register_instance(
        self, tenant_id: str, data: ConnectorInstanceInput
    ) -> ConnectorInstanceView:
        now = self.clock()
        with self.database.transaction() as session:
            if TenantRepository(session).get(tenant_id) is None:
                raise ConnectorNotFoundError(f"tenant not found: {tenant_id}")
            row = ConnectorInstance(
                connector_instance_id=new_id("con"),
                tenant_id=tenant_id,
                connector_key=data.connector_key,
                connector_type=data.connector_type,
                display_name=data.display_name,
                base_url=data.base_url,
                status="active",
                credential_ref=data.credential_ref,
                config_json=data.config,
                created_at=now,
                updated_at=now,
                last_health_details_json={},
            )
            ConnectorRepository(session).add_instance(row)
            self._event(session, tenant_id, row.connector_instance_id, "connector.registered", {
                "connector_type": row.connector_type,
                "connector_key": row.connector_key,
                "credential_ref_present": bool(row.credential_ref),
            })
            return _instance_view(row)

    def create_grant(
        self,
        tenant_id: str,
        connector_instance_id: str,
        data: CollectionGrantInput,
    ) -> CollectionGrantView:
        now = self.clock()
        if data.expires_at is not None and data.expires_at <= now:
            raise CollectionGrantExpiredError("grant expiry must be in the future")
        with self.database.transaction() as session:
            repository = ConnectorRepository(session)
            instance = repository.get_instance(tenant_id, connector_instance_id)
            if instance is None:
                raise ConnectorNotFoundError(f"connector instance not found: {connector_instance_id}")
            row = CollectionGrant(
                grant_id=new_id("grt"),
                tenant_id=tenant_id,
                connector_instance_id=connector_instance_id,
                grant_key=data.grant_key,
                purpose=data.purpose,
                status="active",
                read_only=True,
                allowed_streams_json=data.allowed_streams,
                resource_selectors_json=data.resource_selectors,
                approved_by=data.approved_by,
                approved_at=now,
                expires_at=data.expires_at,
                created_at=now,
                updated_at=now,
            )
            repository.add_grant(row)
            self._event(session, tenant_id, connector_instance_id, "connector.grant_approved", {
                "grant_id": row.grant_id,
                "purpose": row.purpose,
                "allowed_streams": row.allowed_streams_json,
                "resource_selectors": row.resource_selectors_json,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            })
            return _grant_view(row)

    def revoke_grant(
        self,
        tenant_id: str,
        grant_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> CollectionGrantView:
        now = self.clock()
        with self.database.transaction() as session:
            row = ConnectorRepository(session).get_grant(tenant_id, grant_id)
            if row is None:
                raise ConnectorNotFoundError(f"collection grant not found: {grant_id}")
            if row.status != "revoked":
                row.status = "revoked"
                row.revoked_by = actor_id
                row.revoked_at = now
                row.revocation_reason = reason
                row.updated_at = now
                self._event(session, tenant_id, row.connector_instance_id, "connector.grant_revoked", {
                    "grant_id": grant_id,
                    "actor_id": actor_id,
                    "reason": reason,
                })
            return _grant_view(row)

    def run(
        self,
        *,
        tenant_id: str,
        connector_instance_id: str,
        grant_id: str,
        connector: Connector,
        request: CollectionRequest,
        idempotency_key: str,
    ) -> ConnectorRunSummary:
        now = self.clock()
        with self.database.transaction() as session:
            repository = ConnectorRepository(session)
            existing = repository.get_run_by_idempotency(tenant_id, idempotency_key)
            if existing is not None:
                return _run_summary(existing)
            instance = repository.get_instance(tenant_id, connector_instance_id)
            if instance is None:
                raise ConnectorNotFoundError(f"connector instance not found: {connector_instance_id}")
            grant = repository.get_grant(tenant_id, grant_id)
            if grant is None or grant.connector_instance_id != connector_instance_id:
                raise ConnectorNotFoundError(f"collection grant not found: {grant_id}")
            checkpoint = repository.get_checkpoint(tenant_id, connector_instance_id, request.stream)
            checkpoint_before = dict(checkpoint.cursor_json) if checkpoint else {}
            self._validate(instance, grant, connector, request, now)
            run = ConnectorRun(
                run_id=new_id("run"),
                tenant_id=tenant_id,
                connector_instance_id=connector_instance_id,
                grant_id=grant_id,
                stream=request.stream,
                status="running",
                idempotency_key=idempotency_key,
                started_at=now,
                checkpoint_before_json=checkpoint_before,
                checkpoint_after_json=checkpoint_before,
                request_json=request.model_dump(mode="json"),
                metrics_json={},
                created_at=now,
            )
            repository.add_run(run)
            self._event(session, tenant_id, connector_instance_id, "connector.run_started", {
                "run_id": run.run_id,
                "grant_id": grant_id,
                "stream": request.stream,
                "checkpoint_before": checkpoint_before,
            })

        shapes: list[Any] = []
        seen = ingested = unchanged = pages = 0
        checkpoint_after = checkpoint_before
        error: Exception | None = None
        try:
            health = connector.health()
            self._record_health(tenant_id, connector_instance_id, health.status, health.details, health.checked_at)
            if health.status != "healthy":
                raise CollectionGrantError(f"connector health is {health.status}")
            for page in connector.collect_pages(request, checkpoint_before):
                pages += 1
                for item in page.objects:
                    seen += 1
                    shapes.append(_schema_shape(item.payload))
                    with self.database.read_session() as session:
                        object_repository = ConnectorRepository(session)
                        any_prior = object_repository.find_any_collected_version(
                            tenant_id=tenant_id,
                            connector_instance_id=connector_instance_id,
                            stream=request.stream,
                            source_object_id=item.source_object_id,
                            source_version=item.source_version,
                        )
                        if any_prior is not None and any_prior.content_sha256 != item.content_sha256:
                            raise SourceVersionConflictError(
                                "source object reused a version identifier for different content: "
                                f"{item.source_object_id}@{item.source_version}"
                            )
                        prior = object_repository.find_collected_version(
                            tenant_id=tenant_id,
                            connector_instance_id=connector_instance_id,
                            stream=request.stream,
                            source_object_id=item.source_object_id,
                            source_version=item.source_version,
                            content_sha256=item.content_sha256,
                        )
                    if prior is not None:
                        unchanged += 1
                        evidence_id = prior.evidence_id
                    else:
                        acquisition_key = self._acquisition_key(
                            connector_instance_id,
                            request.stream,
                            item.source_object_id,
                            item.source_version,
                            item.content_sha256,
                        )
                        evidence = self.vault.ingest_bytes(
                            tenant_id=tenant_id,
                            payload=item.payload_bytes,
                            source_type=connector.descriptor.connector_type,
                            source_locator=item.source_locator,
                            actor_id=f"connector:{connector_instance_id}",
                            actor_type="service",
                            engagement_id=request.engagement_id,
                            task_id=request.task_id,
                            acquisition_key=acquisition_key,
                            original_filename=item.original_filename,
                            mime_type=item.mime_type,
                            classification=item.classification or request.classification,
                            source_time=item.source_time,
                            accepted=True,
                            tainted=item.tainted,
                            retention_until=request.retention_until.date() if request.retention_until else None,
                            metadata={
                                **item.metadata,
                                "collection_request": page.request_metadata,
                                "connector_instance_id": connector_instance_id,
                                "collection_grant_id": grant_id,
                                "connector_stream": request.stream,
                                "source_object_id": item.source_object_id,
                                "source_version": item.source_version,
                            },
                        )
                        evidence_id = evidence.evidence_id
                        ingested += 1
                    with self.database.transaction() as session:
                        repository = ConnectorRepository(session)
                        repository.add_collected_object(
                            CollectedSourceObject(
                                collected_object_id=new_id("cso"),
                                tenant_id=tenant_id,
                                run_id=run.run_id,
                                connector_instance_id=connector_instance_id,
                                stream=request.stream,
                                source_object_id=item.source_object_id,
                                source_version=item.source_version,
                                source_locator=item.source_locator,
                                content_sha256=item.content_sha256,
                                evidence_id=evidence_id,
                                collected_at=self.clock(),
                                metadata_json={
                                    **item.metadata,
                                    "collection_request": page.request_metadata,
                                },
                            )
                        )
                if page.next_cursor is not None:
                    checkpoint_after = dict(page.next_cursor)
                    with self.database.transaction() as session:
                        ConnectorRepository(session).save_checkpoint(
                            tenant_id=tenant_id,
                            connector_instance_id=connector_instance_id,
                            stream=request.stream,
                            cursor=checkpoint_after,
                            now=self.clock(),
                        )
        except Exception as exc:
            error = exc

        finished = self.clock()
        schema_fingerprint = json_sha256(sorted({_canonical_shape_key(shape) for shape in shapes})) if shapes else None
        with self.database.transaction() as session:
            repository = ConnectorRepository(session)
            row = repository.get_run(tenant_id, run.run_id)
            assert row is not None
            previous = repository.latest_successful_run(
                tenant_id,
                connector_instance_id,
                request.stream,
                exclude_run_id=run.run_id,
            )
            row.objects_seen = seen
            row.objects_ingested = ingested
            row.objects_unchanged = unchanged
            row.checkpoint_after_json = checkpoint_after
            row.schema_fingerprint = schema_fingerprint
            row.schema_drift = bool(
                previous
                and previous.schema_fingerprint
                and schema_fingerprint
                and previous.schema_fingerprint != schema_fingerprint
            )
            row.metrics_json = {"pages": pages}
            row.completed_at = finished
            row.status = (
                "partial" if error and seen > 0 else "failed" if error else "succeeded"
            )
            row.last_error = str(error) if error else None
            self._event(session, tenant_id, connector_instance_id, f"connector.run_{row.status}", {
                "run_id": row.run_id,
                "stream": row.stream,
                "objects_seen": seen,
                "objects_ingested": ingested,
                "objects_unchanged": unchanged,
                "checkpoint_after": checkpoint_after,
                "schema_drift": row.schema_drift,
                "error": row.last_error,
            })
            result = _run_summary(row)
        if error:
            raise error
        return result

    def get_run(self, tenant_id: str, run_id: str) -> ConnectorRunSummary:
        with self.database.read_session() as session:
            row = ConnectorRepository(session).get_run(tenant_id, run_id)
            if row is None:
                raise ConnectorNotFoundError(f"connector run not found: {run_id}")
            return _run_summary(row)

    def list_instances(self, tenant_id: str) -> list[ConnectorInstanceView]:
        with self.database.read_session() as session:
            return [_instance_view(row) for row in ConnectorRepository(session).list_instances(tenant_id)]

    def list_grants(
        self, tenant_id: str, connector_instance_id: str | None = None
    ) -> list[CollectionGrantView]:
        with self.database.read_session() as session:
            return [
                _grant_view(row)
                for row in ConnectorRepository(session).list_grants(
                    tenant_id, connector_instance_id
                )
            ]

    def _validate(
        self,
        instance: ConnectorInstance,
        grant: CollectionGrant,
        connector: Connector,
        request: CollectionRequest,
        now: datetime,
    ) -> None:
        if instance.status != "active":
            raise CollectionGrantError(f"connector instance is {instance.status}")
        if instance.connector_type != connector.descriptor.connector_type:
            raise CollectionGrantError("connector implementation does not match registered type")
        if grant.status != "active":
            raise CollectionGrantError(f"collection grant is {grant.status}")
        if grant.expires_at is not None and grant.expires_at <= now:
            raise CollectionGrantExpiredError("collection grant has expired")
        if not grant.read_only:
            raise CollectionGrantError("write-capable grants are not accepted")
        if request.stream not in connector.descriptor.streams:
            raise CollectionScopeError(f"connector does not expose stream {request.stream!r}")
        if request.stream not in grant.allowed_streams_json:
            raise CollectionScopeError(f"grant does not permit stream {request.stream!r}")
        requested_scope = connector.scope_for(request)
        allowed_scope = grant.resource_selectors_json
        for key, value in requested_scope.items():
            if key not in allowed_scope or not _selector_allows(allowed_scope[key], value):
                raise CollectionScopeError(f"grant does not permit {key}={value!r}")

    def _record_health(
        self,
        tenant_id: str,
        connector_instance_id: str,
        status: str,
        details: dict[str, Any],
        checked_at: datetime,
    ) -> None:
        with self.database.transaction() as session:
            instance = ConnectorRepository(session).get_instance(tenant_id, connector_instance_id)
            if instance is None:
                raise ConnectorNotFoundError(f"connector instance not found: {connector_instance_id}")
            instance.last_health_status = status
            instance.last_health_checked_at = checked_at
            instance.last_health_details_json = details

    @staticmethod
    def _acquisition_key(
        connector_instance_id: str,
        stream: str,
        source_object_id: str,
        source_version: str,
        content_sha256: str,
    ) -> str:
        seed = "\x1f".join(
            [connector_instance_id, stream, source_object_id, source_version, content_sha256]
        ).encode("utf-8")
        return f"connector:{hashlib.sha256(seed).hexdigest()}"

    @staticmethod
    def _event(
        session: Any,
        tenant_id: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = AuditEvent(event_type=event_type, tenant_id=tenant_id, payload=payload)
        AuditEventRepository(session).append(event)
        OutboxRepository(session).add(
            tenant_id=tenant_id,
            aggregate_type="connector",
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=event.event_id,
        )
