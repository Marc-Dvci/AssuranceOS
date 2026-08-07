from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ConnectorHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "degraded", "unavailable", "unauthorized"]
    checked_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream: str = Field(min_length=1, max_length=128)
    scope: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    engagement_id: str | None = None
    task_id: str | None = None
    classification: str = Field(default="internal", min_length=1, max_length=64)
    retention_until: datetime | None = None

    @field_validator("retention_until")
    @classmethod
    def retention_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("retention_until must include a timezone")
        return value


class SourceObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_object_id: str = Field(min_length=1, max_length=512)
    source_version: str = Field(min_length=1, max_length=512)
    source_locator: str = Field(min_length=1)
    payload: dict[str, Any]
    source_time: datetime | None = None
    mime_type: str = "application/json"
    original_filename: str | None = None
    classification: str | None = None
    tainted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def payload_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.payload_bytes).hexdigest()


class ConnectorPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[SourceObject]
    next_cursor: dict[str, Any] | None = None
    request_metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_type: str
    display_name: str
    streams: tuple[str, ...]
    required_read_scopes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    documentation_urls: tuple[str, ...] = ()


class ConnectorInstanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_key: str = Field(min_length=1, max_length=128)
    connector_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    base_url: str | None = None
    credential_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CollectionGrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_key: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=4000)
    allowed_streams: list[str] = Field(min_length=1)
    resource_selectors: dict[str, Any] = Field(default_factory=dict)
    approved_by: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @field_validator("allowed_streams")
    @classmethod
    def unique_streams(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_streams must not contain duplicates")
        return value


class ConnectorRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    connector_instance_id: str
    grant_id: str
    stream: str
    status: Literal["pending", "running", "succeeded", "partial", "failed", "cancelled"]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    checkpoint_before: dict[str, Any] = Field(default_factory=dict)
    checkpoint_after: dict[str, Any] = Field(default_factory=dict)
    objects_seen: int = 0
    objects_ingested: int = 0
    objects_unchanged: int = 0
    schema_fingerprint: str | None = None
    schema_drift: bool = False
    last_error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class ConnectorInstanceView(BaseModel):
    connector_instance_id: str
    tenant_id: str
    connector_key: str
    connector_type: str
    display_name: str
    base_url: str | None = None
    status: str
    credential_ref: str | None = None
    config: dict[str, Any]
    last_health_status: str | None = None
    last_health_checked_at: datetime | None = None
    last_health_details: dict[str, Any]


class CollectionGrantView(BaseModel):
    grant_id: str
    tenant_id: str
    connector_instance_id: str
    grant_key: str
    purpose: str
    status: str
    read_only: bool
    allowed_streams: list[str]
    resource_selectors: dict[str, Any]
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
