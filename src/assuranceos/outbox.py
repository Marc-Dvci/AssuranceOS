from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from assuranceos.db.models import OutboxEvent
from assuranceos.db.repositories import OutboxRepository
from assuranceos.db.session import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PublishedMessage:
    outbox_id: str
    message_id: str
    event_type: str
    tenant_id: str


@dataclass(frozen=True)
class DispatchReport:
    claimed: int
    published: int
    failed: int
    dead_lettered: int
    messages: tuple[PublishedMessage, ...] = ()


class EventPublisher(Protocol):
    def publish(self, event: OutboxEvent) -> str: ...


class InMemoryPublisher:
    """Deterministic publisher for tests and local execution."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish(self, event: OutboxEvent) -> str:
        message_id = f"mem_{uuid4().hex[:20]}"
        self.events.append(
            {
                "message_id": message_id,
                "outbox_id": event.outbox_id,
                "tenant_id": event.tenant_id,
                "event_type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "idempotency_key": event.idempotency_key,
                "occurred_at": event.occurred_at.isoformat(),
                "payload": event.payload_json,
            }
        )
        return message_id


class GooglePubSubPublisher:
    """Lazy Google Cloud Pub/Sub adapter.

    The message carries the outbox idempotency key and canonical identifiers as attributes. The
    database row is marked published only after the server returns a message id.
    """

    def __init__(self, *, project_id: str, topic_id: str, client: object | None = None):
        if client is None:
            try:
                from google.cloud import pubsub_v1
            except ImportError as exc:  # pragma: no cover - optional cloud dependency
                raise RuntimeError("install the cloud extra to use Google Pub/Sub") from exc
            client = pubsub_v1.PublisherClient()
        self._client = client
        self.topic_path = self._client.topic_path(project_id, topic_id)

    def publish(self, event: OutboxEvent) -> str:
        envelope = {
            "specversion": "1.0",
            "id": event.outbox_id,
            "source": "assuranceos",
            "type": event.event_type,
            "subject": f"{event.aggregate_type}/{event.aggregate_id}",
            "time": event.occurred_at.isoformat(),
            "datacontenttype": "application/json",
            "data": event.payload_json,
        }
        future = self._client.publish(
            self.topic_path,
            json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            tenant_id=event.tenant_id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            idempotency_key=event.idempotency_key,
        )
        return str(future.result(timeout=30))


class OutboxDispatcher:
    def __init__(
        self,
        database: Database,
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 10,
        max_backoff_seconds: int = 900,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.database = database
        self.publisher = publisher
        self.clock = clock
        self.max_attempts = max_attempts
        self.max_backoff_seconds = max_backoff_seconds

    def dispatch_once(
        self, *, worker_id: str, limit: int = 100, lease_seconds: int = 60
    ) -> DispatchReport:
        now = self.clock()
        with self.database.transaction() as session:
            claimed = OutboxRepository(session).claim(
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
                limit=limit,
            )
        published: list[PublishedMessage] = []
        failures = dead_lettered = 0
        for event in claimed:
            try:
                message_id = self.publisher.publish(event)
            except Exception as exc:  # publisher boundary must not lose the row
                failures += 1
                attempt_no = event.publish_attempts + 1
                is_dead = attempt_no >= self.max_attempts
                if is_dead:
                    dead_lettered += 1
                delay = min(2 ** max(attempt_no - 1, 0), self.max_backoff_seconds)
                with self.database.transaction() as session:
                    updated = OutboxRepository(session).mark_failed(
                        event.outbox_id,
                        worker_id=worker_id,
                        now=self.clock(),
                        error=f"{type(exc).__name__}: {exc}",
                        retry_delay_seconds=delay,
                        dead_letter=is_dead,
                    )
                    if not updated:
                        raise RuntimeError("outbox lease was lost while recording publish failure")
            else:
                published_at = self.clock()
                with self.database.transaction() as session:
                    updated = OutboxRepository(session).mark_published(
                        event.outbox_id,
                        worker_id=worker_id,
                        published_at=published_at,
                        message_id=message_id,
                    )
                    if not updated:
                        raise RuntimeError("outbox lease was lost after publishing")
                published.append(
                    PublishedMessage(
                        outbox_id=event.outbox_id,
                        message_id=message_id,
                        event_type=event.event_type,
                        tenant_id=event.tenant_id,
                    )
                )
        return DispatchReport(
            claimed=len(claimed),
            published=len(published),
            failed=failures,
            dead_lettered=dead_lettered,
            messages=tuple(published),
        )
