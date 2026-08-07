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
from .common import RestAdapter, parse_timestamp


class JiraIssueConnector(RestAdapter):
    descriptor = ConnectorDescriptor(
        connector_type="jira",
        display_name="Jira Cloud REST v3",
        streams=("issues",),
        required_read_scopes={"issues": ("read:jira-work",)},
        documentation_urls=(
            "https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/",
        ),
    )

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def health(self) -> ConnectorHealth:
        response = self.request("GET", "/rest/api/3/myself", headers=self._headers())
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={"account_id": response.json_body.get("accountId")},
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        projects = request.scope.get("projects")
        if isinstance(projects, str):
            projects = [projects]
        if not isinstance(projects, list) or not projects or not all(isinstance(v, str) for v in projects):
            raise ValueError("Jira request.scope.projects must be a non-empty string list")
        return {"projects": projects}

    def collect_pages(self, request: CollectionRequest, checkpoint: dict[str, object]):
        projects = list(self.scope_for(request)["projects"])
        project_clause = ",".join(projects)
        supplied_jql = str(request.parameters.get("jql") or "").strip()
        bounded_jql = f"project in ({project_clause})"
        jql = f"({bounded_jql}) AND ({supplied_jql})" if supplied_jql else bounded_jql
        token = checkpoint.get("next_page_token")
        fields = request.parameters.get(
            "fields",
            ["summary", "status", "assignee", "reporter", "created", "updated", "resolution", "labels"],
        )
        while True:
            body: dict[str, Any] = {
                "jql": jql,
                "maxResults": min(int(request.parameters.get("page_size", 100)), 100),
                "fields": fields,
                "fieldsByKeys": True,
            }
            if token:
                body["nextPageToken"] = token
            response = self.request(
                "POST",
                "/rest/api/3/search/jql",
                headers=self._headers(),
                json_body=body,
            )
            payload = response.json_body
            issues = payload.get("issues") if isinstance(payload, dict) else None
            if not isinstance(issues, list):
                raise ConnectorProtocolError("Jira search response must contain an issues array")
            objects: list[SourceObject] = []
            for issue in issues:
                fields_payload = issue.get("fields") or {}
                updated = parse_timestamp(fields_payload.get("updated"))
                issue_id = str(issue.get("id") or issue.get("key"))
                key = str(issue.get("key") or issue_id)
                objects.append(
                    SourceObject(
                        source_object_id=issue_id,
                        source_version=str(fields_payload.get("updated") or "unknown"),
                        source_locator=self.url(f"/browse/{key}"),
                        payload=issue,
                        source_time=updated,
                        original_filename=f"jira-{key}.json",
                        metadata={"issue_key": key, "projects": projects, "jql": jql},
                    )
                )
            token = payload.get("nextPageToken")
            yield ConnectorPage(
                objects=objects,
                next_cursor={"next_page_token": token},
                request_metadata={"endpoint": "/rest/api/3/search/jql", "jql": jql},
            )
            if not token:
                break
