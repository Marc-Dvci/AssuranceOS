from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .models import EvidenceReference


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def capture_file(path: Path, source_type: str, classification: str = "internal") -> EvidenceReference:
    payload = path.read_bytes()
    return EvidenceReference(
        evidence_id=f"evd_{hash_bytes(payload)[:20]}",
        source_type=source_type,
        source_locator=str(path),
        sha256=hash_bytes(payload),
        collected_at=datetime.now(timezone.utc),
        classification=classification,
        accepted=True,
    )
