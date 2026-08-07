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


class GoogleDriveFileConnector(RestAdapter):
    descriptor = ConnectorDescriptor(
        connector_type="google_drive",
        display_name="Google Drive API v3",
        streams=("files", "changes"),
        required_read_scopes={
            "files": ("https://www.googleapis.com/auth/drive.metadata.readonly",),
            "changes": ("https://www.googleapis.com/auth/drive.metadata.readonly",),
        },
        documentation_urls=(
            "https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list",
            "https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/list",
            "https://developers.google.com/workspace/drive/api/guides/manage-changes",
        ),
    )

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def health(self) -> ConnectorHealth:
        response = self.request(
            "GET", "/drive/v3/about", params={"fields": "user"}, headers=self._headers()
        )
        user = response.json_body.get("user", {})
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={"permission_id": user.get("permissionId")},
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        drive_ids = request.scope.get("drive_ids", ["my-drive"])
        if isinstance(drive_ids, str):
            drive_ids = [drive_ids]
        if not isinstance(drive_ids, list) or not drive_ids:
            raise ValueError("Google Drive request.scope.drive_ids must be non-empty")
        return {"drive_ids": [str(value) for value in drive_ids]}

    def collect_pages(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        if request.stream == "files":
            yield from self._collect_files(request, checkpoint)
            return
        if request.stream == "changes":
            yield from self._collect_changes(request, checkpoint)
            return
        raise ValueError(f"unsupported Google Drive stream: {request.stream}")

    def _drive_id(self, request: CollectionRequest) -> str:
        drive_ids = list(self.scope_for(request)["drive_ids"])
        if len(drive_ids) != 1:
            raise ValueError("one Drive corpus must be collected per run")
        return drive_ids[0]

    def _collect_files(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        drive_id = self._drive_id(request)
        token = checkpoint.get("page_token")
        query = str(request.parameters.get("q") or "trashed = false")
        while True:
            params: dict[str, Any] = {
                "q": query,
                "pageSize": min(int(request.parameters.get("page_size", 1000)), 1000),
                "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,createdTime,version,md5Checksum,size,parents,driveId,webViewLink,trashed)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "orderBy": "modifiedTime,name",
            }
            if drive_id != "my-drive":
                params.update({"corpora": "drive", "driveId": drive_id})
            if token:
                params["pageToken"] = token
            response = self.request("GET", "/drive/v3/files", params=params, headers=self._headers())
            payload = response.json_body
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                raise ConnectorProtocolError("Google Drive response must contain files")
            objects = [self._file_object(item, drive_id) for item in files]
            token = payload.get("nextPageToken")
            yield ConnectorPage(
                objects=objects,
                next_cursor={"page_token": token},
                request_metadata={
                    "endpoint": "/drive/v3/files",
                    "drive_id": drive_id,
                    "page_token": params.get("pageToken"),
                    "query": query,
                    "incomplete_search": bool(payload.get("incompleteSearch")),
                },
            )
            if not token:
                break

    def _collect_changes(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        drive_id = self._drive_id(request)
        page_token = checkpoint.get("start_page_token")
        if not page_token:
            params: dict[str, Any] = {"supportsAllDrives": "true"}
            if drive_id != "my-drive":
                params["driveId"] = drive_id
            response = self.request(
                "GET", "/drive/v3/changes/startPageToken", params=params, headers=self._headers()
            )
            page_token = response.json_body.get("startPageToken")
            if not page_token:
                raise ConnectorProtocolError("Google Drive did not return a start page token")

        while page_token:
            params = {
                "pageToken": page_token,
                "pageSize": min(int(request.parameters.get("page_size", 1000)), 1000),
                "spaces": "drive",
                "includeRemoved": "true",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "fields": "nextPageToken,newStartPageToken,changes(changeType,time,removed,fileId,driveId,file(id,name,mimeType,modifiedTime,createdTime,version,md5Checksum,size,parents,driveId,webViewLink,trashed))",
            }
            if drive_id != "my-drive":
                params["driveId"] = drive_id
            response = self.request(
                "GET", "/drive/v3/changes", params=params, headers=self._headers()
            )
            payload = response.json_body
            changes = payload.get("changes") if isinstance(payload, dict) else None
            if not isinstance(changes, list):
                raise ConnectorProtocolError("Google Drive response must contain changes")
            objects: list[SourceObject] = []
            for change in changes:
                file_id = str(change.get("fileId") or change.get("driveId"))
                changed_at = parse_timestamp(change.get("time"))
                file_payload = change.get("file") or {}
                version = str(file_payload.get("version") or "unknown")
                change_time = str(change.get("time") or "unknown")
                objects.append(
                    SourceObject(
                        source_object_id=file_id,
                        source_version=(
                            f"{change_time}:{version}:"
                            f"{'removed' if change.get('removed') else 'current'}"
                        ),
                        source_locator=str(
                            file_payload.get("webViewLink")
                            or f"gdrive://{drive_id}/files/{file_id}"
                        ),
                        payload=change,
                        source_time=changed_at,
                        original_filename=f"drive-change-{file_id}.json",
                        metadata={
                            "drive_id": change.get("driveId") or drive_id,
                            "change_type": change.get("changeType"),
                            "removed": bool(change.get("removed")),
                            "incremental": True,
                        },
                    )
                )
            next_page = payload.get("nextPageToken")
            new_start = payload.get("newStartPageToken")
            saved_token = next_page or new_start
            if not saved_token:
                raise ConnectorProtocolError(
                    "Google Drive changes response omitted both nextPageToken and newStartPageToken"
                )
            yield ConnectorPage(
                objects=objects,
                next_cursor={"start_page_token": saved_token},
                request_metadata={
                    "endpoint": "/drive/v3/changes",
                    "drive_id": drive_id,
                    "page_token": page_token,
                    "incremental": True,
                },
            )
            if not next_page:
                break
            page_token = next_page

    def _file_object(self, item: dict[str, Any], drive_id: str) -> SourceObject:
        file_id = str(item.get("id"))
        modified = parse_timestamp(item.get("modifiedTime"))
        version = str(item.get("version") or item.get("modifiedTime") or "unknown")
        return SourceObject(
            source_object_id=file_id,
            source_version=version,
            source_locator=str(item.get("webViewLink") or f"gdrive://{drive_id}/files/{file_id}"),
            payload=item,
            source_time=modified,
            original_filename=f"drive-file-{file_id}.json",
            metadata={
                "drive_id": item.get("driveId") or drive_id,
                "name": item.get("name"),
                "metadata_only": True,
            },
        )
