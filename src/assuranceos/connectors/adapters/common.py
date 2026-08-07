from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from ..credentials import CredentialProvider, NoCredential
from ..transport import HttpRequest, HttpTransport


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class RestAdapter:
    def __init__(
        self,
        base_url: str,
        transport: HttpTransport,
        credential: CredentialProvider | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.credential = credential or NoCredential()

    def url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = self.credential.headers()
        headers.update(extra or {})
        return headers

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        url = path_or_url if "://" in path_or_url else self.url(path_or_url)
        return self.transport.send(
            HttpRequest(
                method=method,
                url=url,
                headers=self.headers(headers),
                params=params or {},
                json_body=json_body,
            )
        )
