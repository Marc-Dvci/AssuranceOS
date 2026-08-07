from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def custody_event_hash(
    *,
    tenant_id: str,
    evidence_id: str,
    sequence_no: int,
    action: str,
    actor_type: str,
    actor_id: str,
    occurred_at: datetime,
    details: dict[str, Any],
    previous_event_hash: str | None,
) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "details": details,
            "evidence_id": evidence_id,
            "occurred_at": canonical_datetime(occurred_at),
            "previous_event_hash": previous_event_hash,
            "sequence_no": sequence_no,
            "tenant_id": tenant_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
