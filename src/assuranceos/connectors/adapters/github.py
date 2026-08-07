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


class GitHubPullRequestConnector(RestAdapter):
    descriptor = ConnectorDescriptor(
        connector_type="github",
        display_name="GitHub REST",
        streams=("pull_requests",),
        required_read_scopes={"pull_requests": ("Pull requests: read",)},
        documentation_urls=(
            "https://docs.github.com/en/rest/pulls/pulls",
            "https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api",
        ),
    )

    def __init__(self, *args: Any, api_version: str = "2026-03-10", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.api_version = api_version

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "AssuranceOS-Connector/0.6",
        }

    def health(self) -> ConnectorHealth:
        response = self.request("GET", "/rate_limit", headers=self._headers())
        core = response.json_body.get("resources", {}).get("core", {})
        return ConnectorHealth(
            status="healthy",
            checked_at=datetime.now(timezone.utc),
            details={
                "remaining": core.get("remaining"),
                "limit": core.get("limit"),
                "reset": core.get("reset"),
                "api_version": self.api_version,
            },
        )

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        repository = request.scope.get("repository")
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise ValueError("GitHub request.scope.repository must be 'owner/repository'")
        return {"repositories": repository}

    def collect_pages(self, request: CollectionRequest, checkpoint: dict[str, object]):
        repository = str(self.scope_for(request)["repositories"])
        owner, repo = repository.split("/", 1)
        page = int(checkpoint.get("next_page", 1) or 1)
        max_updated: datetime | None = None
        while True:
            response = self.request(
                "GET",
                f"/repos/{owner}/{repo}/pulls",
                headers=self._headers(),
                params={
                    "state": request.parameters.get("state", "all"),
                    "sort": "updated",
                    "direction": "asc",
                    "per_page": min(int(request.parameters.get("page_size", 100)), 100),
                    "page": page,
                },
            )
            if not isinstance(response.json_body, list):
                raise ConnectorProtocolError("GitHub pull request response must be a JSON array")
            objects: list[SourceObject] = []
            for raw in response.json_body:
                updated = parse_timestamp(raw.get("updated_at"))
                max_updated = max(filter(None, [max_updated, updated]), default=max_updated)
                number = raw.get("number")
                head_sha = (raw.get("head") or {}).get("sha") or "unknown"
                objects.append(
                    SourceObject(
                        source_object_id=str(raw.get("node_id") or number),
                        source_version=f"{raw.get('updated_at') or 'unknown'}:{head_sha}",
                        source_locator=str(raw.get("html_url") or self.url(f"/{owner}/{repo}/pull/{number}")),
                        payload=raw,
                        source_time=updated,
                        original_filename=f"pull-request-{number}.json",
                        metadata={"repository": repository, "number": number},
                    )
                )
            next_url = parse_link_header(response.headers.get("link") or response.headers.get("Link")).get("next")
            if next_url:
                next_page = int(query_value(next_url, "page") or page + 1)
                cursor = {"next_page": next_page}
            else:
                cursor = {
                    "next_page": 1,
                    "last_completed_updated_at": max_updated.isoformat() if max_updated else None,
                }
            yield ConnectorPage(
                objects=objects,
                next_cursor=cursor,
                request_metadata={
                    "endpoint": f"/repos/{owner}/{repo}/pulls",
                    "page": page,
                    "api_version": self.api_version,
                },
            )
            if not next_url:
                break
            page = next_page
