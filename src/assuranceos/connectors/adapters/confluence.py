from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..definitions import (
    CollectionRequest,
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorPage,
    SourceObject,
)
from ..exceptions import ConnectorProtocolError
from ..pagination import parse_link_header, query_value
from .common import RestAdapter, parse_timestamp


class ConfluencePageConnector(RestAdapter):
    descriptor = ConnectorDescriptor(
        connector_type="confluence",
        display_name="Confluence Cloud REST v2",
        streams=("pages",),
        required_read_scopes={"pages": ("read:page:confluence",)},
        documentation_urls=(
            "https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/",
        ),
    )

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def health(self) -> ConnectorHealth:
        response = self.request(
            "GET", "/wiki/api/v2/spaces", params={"limit": 1}, headers=self._headers()
        )
        count = len(response.json_body.get("results", [])) if isinstance(response.json_body, dict) else 0
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={"visible_space_sample_count": count},
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        spaces = request.scope.get("space_ids")
        if isinstance(spaces, (str, int)):
            spaces = [str(spaces)]
        if not isinstance(spaces, list) or not spaces:
            raise ValueError("Confluence request.scope.space_ids must be non-empty")
        return {"space_ids": [str(value) for value in spaces]}

    def collect_pages(self, request: CollectionRequest, checkpoint: dict[str, object]):
        spaces = list(self.scope_for(request)["space_ids"])
        cursor = checkpoint.get("cursor")
        while True:
            params: dict[str, Any] = {
                "space-id": spaces,
                "body-format": request.parameters.get("body_format", "storage"),
                "limit": min(int(request.parameters.get("page_size", 100)), 250),
            }
            if cursor:
                params["cursor"] = cursor
            response = self.request(
                "GET", "/wiki/api/v2/pages", params=params, headers=self._headers()
            )
            payload = response.json_body
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise ConnectorProtocolError("Confluence page response must contain results")
            objects: list[SourceObject] = []
            for page in results:
                version = page.get("version") or {}
                page_id = str(page.get("id"))
                created = parse_timestamp(version.get("createdAt"))
                webui = (page.get("_links") or {}).get("webui")
                objects.append(
                    SourceObject(
                        source_object_id=page_id,
                        source_version=str(version.get("number") or version.get("createdAt") or "unknown"),
                        source_locator=self.url(webui) if webui else self.url(f"/wiki/pages/viewpage.action?pageId={page_id}"),
                        payload=page,
                        source_time=created,
                        original_filename=f"confluence-page-{page_id}.json",
                        metadata={
                            "space_id": str(page.get("spaceId")),
                            "title": page.get("title"),
                            "body_format": params["body-format"],
                        },
                    )
                )
            links = parse_link_header(response.headers.get("link") or response.headers.get("Link"))
            next_url = links.get("next") or (payload.get("_links") or {}).get("next")
            cursor = query_value(next_url, "cursor") if next_url else None
            yield ConnectorPage(
                objects=objects,
                next_cursor={"cursor": cursor},
                request_metadata={"endpoint": "/wiki/api/v2/pages", "space_ids": spaces},
            )
            if not cursor:
                break
