from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .db.repositories import AuditEventRepository, TenantRepository
from .db.session import Database
from .models import AuditEvent


class AuditLedger:
    """Compatibility facade over the canonical audit-event repository.

    New application code should compose repositories within ``Database.transaction()`` when a
    domain state transition and its outbox/audit events must commit atomically.
    """

    def __init__(self, database: Database | Path | str):
        if isinstance(database, Database):
            self.database = database
        elif isinstance(database, Path):
            self.database = Database.from_sqlite_path(database)
        elif "://" in database:
            self.database = Database(database)
        else:
            self.database = Database.from_sqlite_path(Path(database))
        if not isinstance(database, Database):
            self.database.create_schema()

    def append(self, event: AuditEvent) -> None:
        with self.database.transaction() as session:
            AuditEventRepository(session).append(event)

    def append_many(self, events: Iterable[AuditEvent]) -> None:
        with self.database.transaction() as session:
            AuditEventRepository(session).append_many(events)

    def list_events(self, tenant_id: str, engagement_id: str | None = None) -> list[dict]:
        with self.database.read_session() as session:
            return AuditEventRepository(session).list(tenant_id, engagement_id)

    def reset(self, tenant_id: str) -> int:
        with self.database.transaction() as session:
            return AuditEventRepository(session).delete_for_tenant(tenant_id)

    def reset_tenant(self, tenant_id: str) -> bool:
        with self.database.transaction() as session:
            tenant = TenantRepository(session).get(tenant_id)
            if tenant is None:
                return False
            session.delete(tenant)
            return True
