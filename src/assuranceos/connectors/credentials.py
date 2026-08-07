from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Protocol


class CredentialProvider(Protocol):
    def headers(self) -> dict[str, str]: ...


@dataclass(frozen=True, repr=False)
class StaticHeaderCredential:
    """Test/local credential provider; values are deliberately excluded from repr."""

    values: dict[str, str] = field(default_factory=dict)

    def headers(self) -> dict[str, str]:
        return dict(self.values)

    def __repr__(self) -> str:
        return "StaticHeaderCredential(<redacted>)"


@dataclass(frozen=True)
class NoCredential:
    def headers(self) -> dict[str, str]:
        return {}


@dataclass(frozen=True, repr=False)
class EnvironmentJsonCredential:
    """Resolve a JSON object of HTTP headers from one environment variable."""

    variable_name: str

    def headers(self) -> dict[str, str]:
        raw = os.getenv(self.variable_name)
        if raw is None:
            raise RuntimeError(f"credential environment variable is not set: {self.variable_name}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("credential environment variable is not valid JSON") from exc
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise RuntimeError("credential JSON must be an object of string HTTP headers")
        return dict(value)

    def __repr__(self) -> str:
        return f"EnvironmentJsonCredential({self.variable_name!r}, <redacted>)"


class GoogleSecretManagerCredential:
    """Resolve a JSON header object from Google Secret Manager with a bounded in-memory cache."""

    def __init__(
        self,
        resource_name: str,
        *,
        client: object | None = None,
        cache_ttl_seconds: int = 300,
    ):
        if "/versions/" not in resource_name:
            raise ValueError("Secret Manager resource must include an explicit version")
        self.resource_name = resource_name
        if client is None:
            try:
                from google.cloud import secretmanager
            except ImportError as exc:  # pragma: no cover - optional cloud dependency
                raise RuntimeError("install the cloud extra to use Secret Manager") from exc
            client = secretmanager.SecretManagerServiceClient()
        self._client = client
        self.cache_ttl = timedelta(seconds=max(cache_ttl_seconds, 0))
        self._cached: dict[str, str] | None = None
        self._cached_until: datetime | None = None
        self._lock = Lock()

    def headers(self) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._cached is not None and self._cached_until and now < self._cached_until:
                return dict(self._cached)
            response = self._client.access_secret_version(
                request={"name": self.resource_name}, timeout=15
            )
            raw = bytes(response.payload.data)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Secret Manager credential is not valid JSON") from exc
            if not isinstance(value, dict) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items()
            ):
                raise RuntimeError("Secret Manager credential must be string HTTP headers")
            self._cached = dict(value)
            self._cached_until = now + self.cache_ttl
            return dict(self._cached)

    def __repr__(self) -> str:
        return f"GoogleSecretManagerCredential({self.resource_name!r}, <redacted>)"


class CredentialResolver:
    """Resolve canonical credential references without persisting secret values."""

    def __init__(self, *, secret_manager_client: object | None = None):
        self.secret_manager_client = secret_manager_client

    def resolve(self, reference: str | None) -> CredentialProvider:
        if reference is None:
            return NoCredential()
        if reference.startswith("env://"):
            name = reference.removeprefix("env://")
            if not name:
                raise ValueError("environment credential reference is missing a variable name")
            return EnvironmentJsonCredential(name)
        if reference.startswith("gcp-secret://"):
            resource = reference.removeprefix("gcp-secret://")
            if resource.startswith("projects/"):
                resource_name = resource
            else:
                parts = resource.split("/")
                if len(parts) != 3:
                    raise ValueError(
                        "gcp-secret reference must be project/secret/version or a full resource"
                    )
                project, secret, version = parts
                resource_name = f"projects/{project}/secrets/{secret}/versions/{version}"
            return GoogleSecretManagerCredential(
                resource_name, client=self.secret_manager_client
            )
        raise ValueError("unsupported credential reference scheme")
