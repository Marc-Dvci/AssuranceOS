from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    CollectedSourceObject,
    CollectionGrant,
    ConnectorCheckpoint,
    ConnectorInstance,
    ConnectorRun,
)
from assuranceos.db.repositories import new_id

from .exceptions import ConnectorRunConflictError


class ConnectorRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_instance(self, instance: ConnectorInstance) -> ConnectorInstance:
        self.session.add(instance)
        self._flush("connector instance already exists")
        return instance

    def get_instance(self, tenant_id: str, connector_instance_id: str) -> ConnectorInstance | None:
        return self.session.scalar(
            select(ConnectorInstance).where(
                ConnectorInstance.tenant_id == tenant_id,
                ConnectorInstance.connector_instance_id == connector_instance_id,
            )
        )

    def get_instance_by_key(self, tenant_id: str, connector_key: str) -> ConnectorInstance | None:
        return self.session.scalar(
            select(ConnectorInstance).where(
                ConnectorInstance.tenant_id == tenant_id,
                ConnectorInstance.connector_key == connector_key,
            )
        )

    def list_instances(self, tenant_id: str) -> list[ConnectorInstance]:
        return list(
            self.session.scalars(
                select(ConnectorInstance)
                .where(ConnectorInstance.tenant_id == tenant_id)
                .order_by(ConnectorInstance.connector_key)
            )
        )

    def add_grant(self, grant: CollectionGrant) -> CollectionGrant:
        self.session.add(grant)
        self._flush("collection grant already exists")
        return grant

    def get_grant(self, tenant_id: str, grant_id: str) -> CollectionGrant | None:
        return self.session.scalar(
            select(CollectionGrant).where(
                CollectionGrant.tenant_id == tenant_id,
                CollectionGrant.grant_id == grant_id,
            )
        )

    def list_grants(
        self, tenant_id: str, connector_instance_id: str | None = None
    ) -> list[CollectionGrant]:
        statement = select(CollectionGrant).where(CollectionGrant.tenant_id == tenant_id)
        if connector_instance_id is not None:
            statement = statement.where(
                CollectionGrant.connector_instance_id == connector_instance_id
            )
        return list(self.session.scalars(statement.order_by(CollectionGrant.created_at)))

    def add_run(self, run: ConnectorRun) -> ConnectorRun:
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConnectorRunConflictError("connector run idempotency key already exists") from exc
        return run

    def get_run(self, tenant_id: str, run_id: str) -> ConnectorRun | None:
        return self.session.scalar(
            select(ConnectorRun).where(
                ConnectorRun.tenant_id == tenant_id,
                ConnectorRun.run_id == run_id,
            )
        )

    def get_run_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> ConnectorRun | None:
        return self.session.scalar(
            select(ConnectorRun).where(
                ConnectorRun.tenant_id == tenant_id,
                ConnectorRun.idempotency_key == idempotency_key,
            )
        )

    def latest_successful_run(
        self,
        tenant_id: str,
        connector_instance_id: str,
        stream: str,
        *,
        exclude_run_id: str | None = None,
    ) -> ConnectorRun | None:
        statement = (
            select(ConnectorRun)
            .where(
                ConnectorRun.tenant_id == tenant_id,
                ConnectorRun.connector_instance_id == connector_instance_id,
                ConnectorRun.stream == stream,
                ConnectorRun.status == "succeeded",
            )
            .order_by(ConnectorRun.completed_at.desc(), ConnectorRun.created_at.desc())
            .limit(1)
        )
        if exclude_run_id is not None:
            statement = statement.where(ConnectorRun.run_id != exclude_run_id)
        return self.session.scalar(statement)

    def get_checkpoint(
        self, tenant_id: str, connector_instance_id: str, stream: str
    ) -> ConnectorCheckpoint | None:
        return self.session.scalar(
            select(ConnectorCheckpoint).where(
                ConnectorCheckpoint.tenant_id == tenant_id,
                ConnectorCheckpoint.connector_instance_id == connector_instance_id,
                ConnectorCheckpoint.stream == stream,
            )
        )

    def save_checkpoint(
        self,
        *,
        tenant_id: str,
        connector_instance_id: str,
        stream: str,
        cursor: dict[str, Any],
        now: datetime,
    ) -> ConnectorCheckpoint:
        checkpoint = self.get_checkpoint(tenant_id, connector_instance_id, stream)
        if checkpoint is None:
            checkpoint = ConnectorCheckpoint(
                checkpoint_id=new_id("ckp"),
                tenant_id=tenant_id,
                connector_instance_id=connector_instance_id,
                stream=stream,
                cursor_json=cursor,
                version=1,
                updated_at=now,
            )
            self.session.add(checkpoint)
        else:
            checkpoint.cursor_json = cursor
            checkpoint.version += 1
            checkpoint.updated_at = now
        self.session.flush()
        return checkpoint

    def add_collected_object(self, item: CollectedSourceObject) -> CollectedSourceObject:
        self.session.add(item)
        self.session.flush()
        return item

    def find_any_collected_version(
        self,
        *,
        tenant_id: str,
        connector_instance_id: str,
        stream: str,
        source_object_id: str,
        source_version: str,
    ) -> CollectedSourceObject | None:
        return self.session.scalar(
            select(CollectedSourceObject)
            .where(
                CollectedSourceObject.tenant_id == tenant_id,
                CollectedSourceObject.connector_instance_id == connector_instance_id,
                CollectedSourceObject.stream == stream,
                CollectedSourceObject.source_object_id == source_object_id,
                CollectedSourceObject.source_version == source_version,
            )
            .order_by(CollectedSourceObject.collected_at.desc())
            .limit(1)
        )

    def find_collected_version(
        self,
        *,
        tenant_id: str,
        connector_instance_id: str,
        stream: str,
        source_object_id: str,
        source_version: str,
        content_sha256: str,
    ) -> CollectedSourceObject | None:
        return self.session.scalar(
            select(CollectedSourceObject)
            .where(
                CollectedSourceObject.tenant_id == tenant_id,
                CollectedSourceObject.connector_instance_id == connector_instance_id,
                CollectedSourceObject.stream == stream,
                CollectedSourceObject.source_object_id == source_object_id,
                CollectedSourceObject.source_version == source_version,
                CollectedSourceObject.content_sha256 == content_sha256,
            )
            .order_by(CollectedSourceObject.collected_at.desc())
            .limit(1)
        )

    def list_collected_objects(self, tenant_id: str, run_id: str) -> list[CollectedSourceObject]:
        return list(
            self.session.scalars(
                select(CollectedSourceObject)
                .where(
                    CollectedSourceObject.tenant_id == tenant_id,
                    CollectedSourceObject.run_id == run_id,
                )
                .order_by(CollectedSourceObject.collected_at, CollectedSourceObject.source_object_id)
            )
        )

    def _flush(self, message: str) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConnectorRunConflictError(message) from exc
