from __future__ import annotations

import email.utils
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import sleep
from typing import Any, Callable, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .exceptions import (
    ConnectorAuthenticationError,
    ConnectorPermissionError,
    ConnectorProtocolError,
    ConnectorRateLimitError,
    ConnectorUnavailableError,
)


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    json_body: Any


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


def normalized_url(url: str, params: dict[str, Any] | None = None) -> str:
    parts = urlsplit(url)
    pairs = list(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    query = urlencode(sorted(pairs), doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _retry_after_seconds(headers: dict[str, str], now: datetime | None = None) -> float | None:
    lower = {key.lower(): value for key, value in headers.items()}
    if value := lower.get("retry-after"):
        try:
            return max(0.0, float(value))
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - (now or datetime.now(timezone.utc))).total_seconds())
    if value := lower.get("x-ratelimit-reset"):
        try:
            return max(0.0, float(value) - (now or datetime.now(timezone.utc)).timestamp())
        except ValueError:
            return None
    return None


def validate_response(response: HttpResponse) -> HttpResponse:
    if 200 <= response.status_code < 300:
        return response
    if response.status_code == 401:
        raise ConnectorAuthenticationError("connector authentication failed")
    if response.status_code == 403:
        lower = {key.lower(): value for key, value in response.headers.items()}
        if lower.get("x-ratelimit-remaining") == "0" or "retry-after" in lower:
            raise ConnectorRateLimitError(
                "connector rate limit exceeded",
                retry_after_seconds=_retry_after_seconds(response.headers),
            )
        raise ConnectorPermissionError("connector access was denied")
    if response.status_code == 429:
        raise ConnectorRateLimitError(
            "connector rate limit exceeded",
            retry_after_seconds=_retry_after_seconds(response.headers),
        )
    if response.status_code >= 500:
        raise ConnectorUnavailableError(f"connector returned HTTP {response.status_code}")
    raise ConnectorProtocolError(f"connector returned HTTP {response.status_code}")


class HttpxTransport:
    """Small synchronous transport with bounded retries for read-only calls."""

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        sleep_fn: Callable[[float], None] = sleep,
        client: Any | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        self.sleep_fn = sleep_fn
        self._client = client

    def send(self, request: HttpRequest) -> HttpResponse:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - packaging dependency protects this
            raise RuntimeError("httpx is required for live connectors") from exc

        client = self._client or httpx.Client(follow_redirects=False)
        own_client = self._client is None
        try:
            for attempt in range(1, self.max_attempts + 1):
                raw = client.request(
                    request.method,
                    request.url,
                    headers=request.headers,
                    params=request.params,
                    json=request.json_body,
                    timeout=request.timeout_seconds,
                )
                try:
                    body = raw.json()
                except ValueError:
                    body = {"text": raw.text}
                response = HttpResponse(
                    status_code=raw.status_code,
                    headers=dict(raw.headers),
                    json_body=body,
                )
                try:
                    return validate_response(response)
                except (ConnectorRateLimitError, ConnectorUnavailableError) as exc:
                    if attempt == self.max_attempts:
                        raise
                    delay = getattr(exc, "retry_after_seconds", None)
                    self.sleep_fn(min(float(delay if delay is not None else 2 ** (attempt - 1)), 60.0))
            raise AssertionError("unreachable")
        finally:
            if own_client:
                client.close()


class FixtureTransport:
    """Deterministic HTTP cassette transport for local demos and contract tests."""

    def __init__(self, responses: dict[tuple[str, str], list[HttpResponse]]):
        self._responses = {key: list(value) for key, value in responses.items()}
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        key = (request.method.upper(), normalized_url(request.url, request.params))
        queue = self._responses.get(key)
        if not queue:
            raise AssertionError(f"no fixture response for {key[0]} {key[1]}")
        return validate_response(queue.pop(0))
