from __future__ import annotations

from sqlalchemy import func, select

from assuranceos.db.models import CollectedSourceObject, EvidenceRecord, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.vault import EvidenceVault

from .adapters import (
    ConfluencePageConnector,
    GitHubPullRequestConnector,
    GoogleDriveFileConnector,
    JiraIssueConnector,
)
from .definitions import CollectionGrantInput, CollectionRequest, ConnectorInstanceInput
from .service import ConnectorService
from .transport import FixtureTransport, HttpResponse, normalized_url

TENANT_ID = "tnt_asteria_connectors"


def _response(body, headers=None):
    return HttpResponse(status_code=200, headers=headers or {}, json_body=body)


def _route(method: str, url: str, params=None):
    return (method, normalized_url(url, params or {}))


def _github() -> GitHubPullRequestConnector:
    base = "https://api.github.asteria.test"
    params = {
        "state": "all",
        "sort": "updated",
        "direction": "asc",
        "per_page": 100,
        "page": 1,
    }
    transport = FixtureTransport(
        {
            _route("GET", f"{base}/rate_limit"): [
                _response({"resources": {"core": {"remaining": 4998, "limit": 5000, "reset": 0}}})
            ],
            _route("GET", f"{base}/repos/asteria/platform/pulls", params): [
                _response(
                    [
                        {
                            "id": 1001,
                            "node_id": "PR_ASTERIA_42",
                            "number": 42,
                            "title": "Require deployment approval evidence",
                            "state": "closed",
                            "merged_at": "2026-07-22T10:40:00Z",
                            "updated_at": "2026-07-22T10:40:00Z",
                            "html_url": "https://github.asteria.test/asteria/platform/pull/42",
                            "head": {"sha": "a1b2c3d4"},
                            "user": {"login": "alice.engineer"},
                        },
                        {
                            "id": 1002,
                            "node_id": "PR_ASTERIA_43",
                            "number": 43,
                            "title": "Emergency production change",
                            "state": "closed",
                            "merged_at": "2026-07-24T02:15:00Z",
                            "updated_at": "2026-07-24T02:15:00Z",
                            "html_url": "https://github.asteria.test/asteria/platform/pull/43",
                            "head": {"sha": "e5f6a7b8"},
                            "user": {"login": "service-release"},
                        },
                    ]
                )
            ],
        }
    )
    return GitHubPullRequestConnector(base, transport)


def _jira() -> JiraIssueConnector:
    base = "https://asteria.atlassian.test"
    transport = FixtureTransport(
        {
            _route("GET", f"{base}/rest/api/3/myself"): [
                _response({"accountId": "asteria-connector"})
            ],
            _route("POST", f"{base}/rest/api/3/search/jql"): [
                _response(
                    {
                        "issues": [
                            {
                                "id": "2001",
                                "key": "CHANGE-42",
                                "fields": {
                                    "summary": "Deploy approved access policy",
                                    "status": {"name": "Done"},
                                    "created": "2026-07-21T09:00:00.000+0000",
                                    "updated": "2026-07-22T10:30:00.000+0000",
                                    "labels": ["production", "approved"],
                                },
                            },
                            {
                                "id": "2002",
                                "key": "CHANGE-43",
                                "fields": {
                                    "summary": "Emergency production patch",
                                    "status": {"name": "Done"},
                                    "created": "2026-07-24T01:50:00.000+0000",
                                    "updated": "2026-07-24T02:10:00.000+0000",
                                    "labels": ["production", "emergency"],
                                },
                            },
                        ]
                    }
                )
            ],
        }
    )
    return JiraIssueConnector(base, transport)


def _confluence() -> ConfluencePageConnector:
    base = "https://asteria.atlassian.test"
    params = {"space-id": ["100"], "body-format": "storage", "limit": 100}
    transport = FixtureTransport(
        {
            _route("GET", f"{base}/wiki/api/v2/spaces", {"limit": 1}): [
                _response({"results": [{"id": "100", "key": "GOV"}]})
            ],
            _route("GET", f"{base}/wiki/api/v2/pages", params): [
                _response(
                    {
                        "results": [
                            {
                                "id": "3001",
                                "spaceId": "100",
                                "status": "current",
                                "title": "Software Change Management Policy",
                                "version": {
                                    "number": 7,
                                    "createdAt": "2026-06-15T08:00:00Z",
                                },
                                "body": {
                                    "storage": {
                                        "representation": "storage",
                                        "value": "<p>Production changes require approval and linked evidence.</p>",
                                    }
                                },
                                "_links": {"webui": "/wiki/spaces/GOV/pages/3001"},
                            }
                        ]
                    }
                )
            ],
        }
    )
    return ConfluencePageConnector(base, transport)


def _drive() -> GoogleDriveFileConnector:
    base = "https://www.googleapis.asteria.test"
    params = {
        "q": "trashed = false",
        "pageSize": 1000,
        "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,createdTime,version,md5Checksum,size,parents,driveId,webViewLink,trashed)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "orderBy": "modifiedTime,name",
    }
    transport = FixtureTransport(
        {
            _route("GET", f"{base}/drive/v3/about", {"fields": "user"}): [
                _response({"user": {"permissionId": "drive-connector"}})
            ],
            _route("GET", f"{base}/drive/v3/files", params): [
                _response(
                    {
                        "files": [
                            {
                                "id": "4001",
                                "name": "SCM Control Matrix.xlsx",
                                "mimeType": "application/vnd.google-apps.spreadsheet",
                                "createdTime": "2026-06-01T08:00:00Z",
                                "modifiedTime": "2026-07-30T14:00:00Z",
                                "version": "12",
                                "parents": ["governance-folder"],
                                "webViewLink": "https://drive.asteria.test/open?id=4001",
                                "trashed": False,
                            }
                        ]
                    }
                )
            ],
        }
    )
    return GoogleDriveFileConnector(base, transport)


def run_connector_demo(database: Database, vault: EvidenceVault) -> dict:
    with database.transaction() as session:
        if TenantRepository(session).get(TENANT_ID) is None:
            TenantRepository(session).add(
                Tenant(
                    tenant_id=TENANT_ID,
                    slug="asteria-connectors",
                    name="Asteria Systems DemoCo Connector Tenant",
                )
            )

    service = ConnectorService(database, vault)
    definitions = [
        (
            "github-main",
            "github",
            "GitHub Main",
            "https://api.github.asteria.test",
            _github(),
            CollectionRequest(
                stream="pull_requests", scope={"repository": "asteria/platform"}
            ),
            {"repositories": ["asteria/platform"]},
        ),
        (
            "jira-change",
            "jira",
            "Jira Change",
            "https://asteria.atlassian.test",
            _jira(),
            CollectionRequest(stream="issues", scope={"projects": ["CHANGE"]}),
            {"projects": ["CHANGE"]},
        ),
        (
            "confluence-governance",
            "confluence",
            "Confluence Governance",
            "https://asteria.atlassian.test",
            _confluence(),
            CollectionRequest(stream="pages", scope={"space_ids": ["100"]}),
            {"space_ids": ["100"]},
        ),
        (
            "drive-governance",
            "google_drive",
            "Google Drive Governance",
            "https://www.googleapis.asteria.test",
            _drive(),
            CollectionRequest(stream="files", scope={"drive_ids": ["my-drive"]}),
            {"drive_ids": ["my-drive"]},
        ),
    ]

    runs = []
    for key, connector_type, display_name, base_url, connector, request, selectors in definitions:
        instance = service.register_instance(
            TENANT_ID,
            ConnectorInstanceInput(
                connector_key=key,
                connector_type=connector_type,
                display_name=display_name,
                base_url=base_url,
                credential_ref=f"secret://demo/{key}",
                config={"fixture_mode": True},
            ),
        )
        grant = service.create_grant(
            TENANT_ID,
            instance.connector_instance_id,
            CollectionGrantInput(
                grant_key=f"{key}-scm-audit",
                purpose="Asteria software change management golden engagement",
                allowed_streams=[request.stream],
                resource_selectors=selectors,
                approved_by="demo-audit-owner",
            ),
        )
        runs.append(
            service.run(
                tenant_id=TENANT_ID,
                connector_instance_id=instance.connector_instance_id,
                grant_id=grant.grant_id,
                connector=connector,
                request=request,
                idempotency_key=f"demo:{key}:2026-08-06",
            ).model_dump(mode="json")
        )

    with database.read_session() as session:
        evidence_count = session.scalar(
            select(func.count(EvidenceRecord.evidence_id)).where(
                EvidenceRecord.tenant_id == TENANT_ID
            )
        )
        source_object_count = session.scalar(
            select(func.count(CollectedSourceObject.collected_object_id)).where(
                CollectedSourceObject.tenant_id == TENANT_ID
            )
        )
    return {
        "tenant_id": TENANT_ID,
        "runs": runs,
        "evidence_count": int(evidence_count or 0),
        "source_object_count": int(source_object_count or 0),
        "all_succeeded": all(run["status"] == "succeeded" for run in runs),
    }
