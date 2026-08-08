from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

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


class OktaDirectoryConnector(RestAdapter):
    """Read-only Okta directory collector with group-bounded user collection."""

    descriptor = ConnectorDescriptor(
        connector_type="okta",
        display_name="Okta Management API",
        streams=("groups", "users"),
        required_read_scopes={
            "groups": ("okta.groups.read",),
            "users": ("okta.groups.read", "okta.users.read"),
        },
        documentation_urls=(
            "https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Group/",
        ),
    )

    def health(self) -> ConnectorHealth:
        response = self.request("GET", "/api/v1/org", headers={"Accept": "application/json"})
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={"organization": response.json_body.get("companyName")},
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        group_ids = request.scope.get("group_ids")
        if isinstance(group_ids, str):
            group_ids = [group_ids]
        if (
            not isinstance(group_ids, list)
            or not group_ids
            or not all(isinstance(value, str) and value for value in group_ids)
        ):
            raise ValueError("Okta request.scope.group_ids must be a non-empty string list")
        return {"group_ids": group_ids}

    def collect_pages(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        group_ids = list(self.scope_for(request)["group_ids"])
        start_group = int(checkpoint.get("group_index", 0))
        after = checkpoint.get("after")
        for index, group_id in enumerate(group_ids[start_group:], start=start_group):
            path = (
                f"/api/v1/groups/{group_id}"
                if request.stream == "groups"
                else f"/api/v1/groups/{group_id}/users"
            )
            params: dict[str, Any] = {
                "limit": min(int(request.parameters.get("page_size", 200)), 200)
            }
            if after and index == start_group:
                params["after"] = after
            while True:
                response = self.request(
                    "GET", path, params=params, headers={"Accept": "application/json"}
                )
                records = response.json_body
                if request.stream == "groups" and isinstance(records, dict):
                    records = [records]
                if not isinstance(records, list):
                    raise ConnectorProtocolError("Okta directory response must be an array")
                objects = [self._object(item, request.stream, group_id) for item in records]
                next_url = parse_link_header(
                    response.headers.get("Link") or response.headers.get("link")
                ).get("next")
                next_after = query_value(next_url, "after") if next_url else None
                yield ConnectorPage(
                    objects=objects,
                    next_cursor={
                        "group_index": index if next_after else index + 1,
                        "after": next_after,
                    },
                    request_metadata={"endpoint": path, "group_id": group_id},
                )
                if not next_after or request.stream == "groups":
                    break
                params["after"] = next_after
            after = None

    def _object(self, item: dict[str, Any], stream: str, group_id: str) -> SourceObject:
        object_id = str(item.get("id") or "")
        if not object_id:
            raise ConnectorProtocolError("Okta object omitted id")
        updated = parse_timestamp(item.get("lastUpdated"))
        return SourceObject(
            source_object_id=object_id,
            source_version=str(item.get("lastUpdated") or item.get("created") or "unknown"),
            source_locator=self.url(
                f"/admin/{'group' if stream == 'groups' else 'user'}/{object_id}"
            ),
            payload=item,
            source_time=updated,
            original_filename=f"okta-{stream}-{object_id}.json",
            metadata={"stream": stream, "bounded_by_group": group_id, "read_only": True},
        )
