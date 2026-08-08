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
from .common import RestAdapter, parse_timestamp


class EntraDirectoryConnector(RestAdapter):
    """Microsoft Graph directory collector constrained to approved object IDs."""

    descriptor = ConnectorDescriptor(
        connector_type="entra",
        display_name="Microsoft Entra ID via Graph",
        streams=("users", "groups", "group_members", "directory_roles"),
        required_read_scopes={
            "users": ("User.Read.All",),
            "groups": ("Group.Read.All",),
            "group_members": ("GroupMember.Read.All",),
            "directory_roles": ("RoleManagement.Read.Directory",),
        },
        documentation_urls=("https://learn.microsoft.com/graph/api/resources/azure-ad-overview",),
    )

    def health(self) -> ConnectorHealth:
        response = self.request("GET", "/v1.0/organization", params={"$select": "id,displayName"})
        values = response.json_body.get("value", [])
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={"organizations": len(values)},
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        key = "group_ids" if request.stream in {"groups", "group_members"} else "object_ids"
        values = request.scope.get(key)
        if isinstance(values, str):
            values = [values]
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise ValueError(f"Entra request.scope.{key} must be a non-empty string list")
        return {key: values}

    def collect_pages(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        scope = self.scope_for(request)
        ids = list(next(iter(scope.values())))
        start = int(checkpoint.get("object_index", 0))
        for index, object_id in enumerate(ids[start:], start=start):
            if request.stream == "group_members":
                path = f"/v1.0/groups/{object_id}/transitiveMembers"
            elif request.stream == "directory_roles":
                path = f"/v1.0/directoryRoles/{object_id}/members"
            else:
                path = f"/v1.0/{request.stream}/{object_id}"
            next_url: str | None = (
                str(checkpoint.get("next_url"))
                if index == start and checkpoint.get("next_url")
                else path
            )
            while next_url:
                response = self.request(
                    "GET",
                    next_url,
                    params={"$top": min(int(request.parameters.get("page_size", 100)), 999)}
                    if next_url == path and request.stream in {"group_members", "directory_roles"}
                    else None,
                )
                payload = response.json_body
                records = (
                    payload.get("value")
                    if isinstance(payload, dict) and "value" in payload
                    else [payload]
                )
                if not isinstance(records, list):
                    raise ConnectorProtocolError(
                        "Microsoft Graph response must contain value array"
                    )
                objects = [self._object(item, request.stream, object_id) for item in records]
                next_value = payload.get("@odata.nextLink") if isinstance(payload, dict) else None
                yield ConnectorPage(
                    objects=objects,
                    next_cursor={
                        "object_index": index if next_value else index + 1,
                        "next_url": next_value,
                    },
                    request_metadata={"endpoint": path, "scope_object_id": object_id},
                )
                next_url = str(next_value) if next_value else None

    def _object(self, item: dict[str, Any], stream: str, scope_id: str) -> SourceObject:
        object_id = str(item.get("id") or "")
        if not object_id:
            raise ConnectorProtocolError("Microsoft Graph object omitted id")
        modified = parse_timestamp(item.get("lastModifiedDateTime"))
        version = str(item.get("lastModifiedDateTime") or item.get("@odata.etag") or "current")
        return SourceObject(
            source_object_id=object_id,
            source_version=version,
            source_locator=f"entra://{stream}/{object_id}",
            payload=item,
            source_time=modified,
            original_filename=f"entra-{stream}-{object_id}.json",
            metadata={"stream": stream, "scope_object_id": scope_id, "read_only": True},
        )
