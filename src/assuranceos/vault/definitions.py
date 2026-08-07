from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RecordKind = Literal["original", "derived"]
IntegrityStatus = Literal["unverified", "verified", "mismatch", "missing", "purged"]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    tenant_id: str
    engagement_id: str | None = None
    task_id: str | None = None
    acquisition_key: str | None = None
    record_kind: RecordKind
    source_type: str
    source_locator: str
    content_sha256: str
    storage_provider: str
    storage_key: str | None = None
    object_uri: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    content_encoding: str | None = None
    size_bytes: int
    classification: str
    source_time: datetime | None = None
    collected_at: datetime
    accepted: bool
    tainted: bool
    integrity_status: IntegrityStatus
    last_verified_at: datetime | None = None
    retention_until: date | None = None
    legal_hold: bool
    deleted_at: datetime | None = None
    deletion_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CustodyEventItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custody_event_id: str
    evidence_id: str
    sequence_no: int
    action: str
    actor_type: str
    actor_id: str
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
    previous_event_hash: str | None = None
    event_hash: str


class IntegrityReport(BaseModel):
    evidence_id: str
    status: IntegrityStatus
    expected_sha256: str
    actual_sha256: str | None = None
    expected_size: int
    actual_size: int | None = None
    verified_at: datetime


class CustodyVerification(BaseModel):
    evidence_id: str
    valid: bool
    event_count: int
    head_hash: str | None = None
    error: str | None = None


class LineageEdge(BaseModel):
    transformation_id: str
    source_evidence_id: str
    derived_evidence_id: str
    operation: str
    tool_version: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class LineageGraph(BaseModel):
    root_evidence_id: str
    nodes: list[EvidenceItem]
    edges: list[LineageEdge]


class ExportVerification(BaseModel):
    valid: bool
    package_sha256: str
    manifest_sha256: str | None = None
    evidence_count: int = 0
    object_count: int = 0
    signature_valid: bool | None = None
    signing_key_id: str | None = None
    errors: list[str] = Field(default_factory=list)


class GarbageCollectionReport(BaseModel):
    tenant_id: str
    examined: int
    deleted: int
    retained: int
    deleted_keys: list[str] = Field(default_factory=list)
