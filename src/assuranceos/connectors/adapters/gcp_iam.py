from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from ..definitions import (
    CollectionRequest,
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorPage,
    SourceObject,
)
from ..exceptions import ConnectorProtocolError
from .common import RestAdapter


class GoogleCloudIamConnector(RestAdapter):
    """Collect project IAM policies without requesting mutation permissions."""

    descriptor = ConnectorDescriptor(
        connector_type="gcp_iam",
        display_name="Google Cloud IAM",
        streams=("project_iam_policies",),
        required_read_scopes={
            "project_iam_policies": ("https://www.googleapis.com/auth/cloud-platform.read-only",)
        },
        documentation_urls=(
            "https://cloud.google.com/resource-manager/reference/rest/v1/projects/getIamPolicy",
        ),
    )

    def health(self) -> ConnectorHealth:
        response = self.request("GET", "/v1/projects", params={"pageSize": 1})
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={"readable_project_seen": bool(response.json_body.get("projects"))},
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        project_ids = request.scope.get("project_ids")
        if isinstance(project_ids, str):
            project_ids = [project_ids]
        if (
            not isinstance(project_ids, list)
            or not project_ids
            or not all(isinstance(value, str) and value for value in project_ids)
        ):
            raise ValueError(
                "Google Cloud IAM request.scope.project_ids must be a non-empty string list"
            )
        return {"project_ids": project_ids}

    def collect_pages(
        self, request: CollectionRequest, checkpoint: dict[str, object]
    ) -> Iterator[ConnectorPage]:
        project_ids = list(self.scope_for(request)["project_ids"])
        start = int(checkpoint.get("project_index", 0))
        for index, project_id in enumerate(project_ids[start:], start=start):
            path = f"/v1/projects/{project_id}:getIamPolicy"
            response = self.request(
                "POST",
                path,
                json_body={"options": {"requestedPolicyVersion": 3}},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            policy = response.json_body
            if not isinstance(policy, dict) or "bindings" not in policy:
                raise ConnectorProtocolError("Google Cloud IAM response omitted policy bindings")
            etag = str(policy.get("etag") or "unknown")
            yield ConnectorPage(
                objects=[
                    SourceObject(
                        source_object_id=project_id,
                        source_version=f"v{policy.get('version', 1)}:{etag}",
                        source_locator=f"gcp://projects/{project_id}/iamPolicy",
                        payload=policy,
                        original_filename=f"gcp-iam-{project_id}.json",
                        metadata={
                            "project_id": project_id,
                            "requested_policy_version": 3,
                            "read_only": True,
                        },
                    )
                ],
                next_cursor={"project_index": index + 1},
                request_metadata={"endpoint": path, "project_id": project_id},
            )
