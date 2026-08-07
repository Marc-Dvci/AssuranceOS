from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from assuranceos.connectors import (
    CollectionGrantInput,
    CollectionRequest,
    ConnectorInstanceInput,
    ConnectorService,
    FixtureTransport,
    HttpResponse,
)
from assuranceos.connectors.adapters import (
    ConfluencePageConnector,
    GitHubPullRequestConnector,
    GoogleDriveFileConnector,
    JiraIssueConnector,
)
from assuranceos.connectors.exceptions import (
    CollectionGrantError,
    CollectionGrantExpiredError,
    CollectionScopeError,
    ConnectorRateLimitError,
)
from assuranceos.connectors.pagination import parse_link_header
from assuranceos.connectors.transport import normalized_url, validate_response
from assuranceos.db.models import CollectedSourceObject, ConnectorCheckpoint, ConnectorRun, EvidenceRecord, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.vault import EvidenceVault


@pytest.fixture
def database(tmp_path: Path):
    db = Database.from_sqlite_path(tmp_path / "connectors.db")
    db.create_schema()
    with db.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def service(database: Database, tmp_path: Path) -> ConnectorService:
    return ConnectorService(database, EvidenceVault.local(database, tmp_path / "objects"))


def response(body, *, headers=None, status=200):
    return HttpResponse(status_code=status, headers=headers or {}, json_body=body)


def route(method: str, url: str, params=None):
    return (method, normalized_url(url, params or {}))


def github_transport(*, changed_shape: bool = False, mutate_same_version: bool = False) -> FixtureTransport:
    base = "https://api.github.test"
    pr1 = {
        "id": 1,
        "node_id": "PR_1",
        "number": 1,
        "updated_at": "2026-08-01T10:00:00Z",
        "html_url": "https://github.test/asteria/platform/pull/1",
        "head": {"sha": "aaa"},
        "title": "Approved change",
    }
    pr2 = {
        "id": 2,
        "node_id": "PR_2",
        "number": 2,
        "updated_at": "2026-08-02T10:00:00Z",
        "html_url": "https://github.test/asteria/platform/pull/2",
        "head": {"sha": "bbb"},
        "title": "Emergency change",
    }
    if changed_shape:
        pr2["review_decision"] = {"state": "APPROVED"}
        pr2["updated_at"] = "2026-08-03T10:00:00Z"
        pr2["head"]["sha"] = "ccc"
    if mutate_same_version:
        pr2["title"] = "Mutated without a new version"
    common = {"state": "all", "sort": "updated", "direction": "asc", "per_page": 100}
    page1 = {**common, "page": 1}
    page2 = {**common, "page": 2}
    return FixtureTransport(
        {
            route("GET", f"{base}/rate_limit"): [response({"resources": {"core": {"remaining": 4999, "limit": 5000, "reset": 0}}})],
            route("GET", f"{base}/repos/asteria/platform/pulls", page1): [
                response([pr1], headers={"Link": f'<{base}/repos/asteria/platform/pulls?page=2>; rel="next"'})
            ],
            route("GET", f"{base}/repos/asteria/platform/pulls", page2): [response([pr2])],
        }
    )


def register_github(service: ConnectorService, *, expires_at=None):
    instance = service.register_instance(
        "tnt_a",
        ConnectorInstanceInput(
            connector_key="github-main",
            connector_type="github",
            display_name="GitHub Main",
            base_url="https://api.github.test",
            credential_ref="secret://github/main",
        ),
    )
    grant = service.create_grant(
        "tnt_a",
        instance.connector_instance_id,
        CollectionGrantInput(
            grant_key="scm-audit",
            purpose="SCM audit evidence collection",
            allowed_streams=["pull_requests"],
            resource_selectors={"repositories": ["asteria/platform"]},
            approved_by="audit-owner",
            expires_at=expires_at,
        ),
    )
    return instance, grant


def test_github_collection_is_paginated_checkpointed_and_idempotent(service: ConnectorService, database: Database):
    instance, grant = register_github(service)
    request = CollectionRequest(stream="pull_requests", scope={"repository": "asteria/platform"})
    first = service.run(
        tenant_id="tnt_a",
        connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id,
        connector=GitHubPullRequestConnector("https://api.github.test", github_transport()),
        request=request,
        idempotency_key="run-1",
    )
    assert first.status == "succeeded"
    assert first.objects_seen == 2
    assert first.objects_ingested == 2
    assert first.objects_unchanged == 0
    assert first.checkpoint_after["next_page"] == 1
    assert first.metrics["pages"] == 2

    second = service.run(
        tenant_id="tnt_a",
        connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id,
        connector=GitHubPullRequestConnector("https://api.github.test", github_transport()),
        request=request,
        idempotency_key="run-2",
    )
    assert second.objects_seen == 2
    assert second.objects_ingested == 0
    assert second.objects_unchanged == 2

    with database.read_session() as session:
        evidence_rows = list(session.scalars(select(EvidenceRecord)))
        assert len(evidence_rows) == 2
        assert all("collection_request" in row.metadata_json for row in evidence_rows)
        assert {row.metadata_json["collection_request"]["page"] for row in evidence_rows} == {1, 2}
        assert len(list(session.scalars(select(CollectedSourceObject)))) == 4
        checkpoint = session.scalar(select(ConnectorCheckpoint))
        assert checkpoint is not None and checkpoint.version == 4
        runs = list(session.scalars(select(ConnectorRun).order_by(ConnectorRun.created_at)))
        assert [row.status for row in runs] == ["succeeded", "succeeded"]


def test_run_idempotency_returns_existing_run_without_recollection(service: ConnectorService):
    instance, grant = register_github(service)
    connector = GitHubPullRequestConnector("https://api.github.test", github_transport())
    request = CollectionRequest(stream="pull_requests", scope={"repository": "asteria/platform"})
    first = service.run(
        tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id, connector=connector, request=request, idempotency_key="same"
    )
    second = service.run(
        tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id, connector=connector, request=request, idempotency_key="same"
    )
    assert second.run_id == first.run_id


def test_grant_scope_stream_expiry_and_revocation_fail_closed(service: ConnectorService):
    instance, grant = register_github(service)
    connector = GitHubPullRequestConnector("https://api.github.test", github_transport())
    with pytest.raises(CollectionScopeError):
        service.run(
            tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
            grant_id=grant.grant_id, connector=connector,
            request=CollectionRequest(stream="pull_requests", scope={"repository": "other/repo"}),
            idempotency_key="denied-scope",
        )

    service.revoke_grant("tnt_a", grant.grant_id, actor_id="owner", reason="access removed")
    with pytest.raises(CollectionGrantError, match="revoked"):
        service.run(
            tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
            grant_id=grant.grant_id, connector=connector,
            request=CollectionRequest(stream="pull_requests", scope={"repository": "asteria/platform"}),
            idempotency_key="denied-revoked",
        )

    with pytest.raises(CollectionGrantExpiredError):
        service.create_grant(
            "tnt_a", instance.connector_instance_id,
            CollectionGrantInput(
                grant_key="expired", purpose="old", allowed_streams=["pull_requests"],
                resource_selectors={"repositories": ["asteria/platform"]}, approved_by="owner",
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
        )


def test_schema_drift_is_detected_between_successful_runs(service: ConnectorService):
    instance, grant = register_github(service)
    request = CollectionRequest(stream="pull_requests", scope={"repository": "asteria/platform"})
    service.run(
        tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id,
        connector=GitHubPullRequestConnector("https://api.github.test", github_transport()),
        request=request, idempotency_key="baseline",
    )
    changed = service.run(
        tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id,
        connector=GitHubPullRequestConnector("https://api.github.test", github_transport(changed_shape=True)),
        request=request, idempotency_key="changed",
    )
    assert changed.schema_drift is True


def test_reused_source_version_with_different_bytes_fails_closed(service: ConnectorService):
    from assuranceos.connectors.exceptions import SourceVersionConflictError

    instance, grant = register_github(service)
    request = CollectionRequest(stream="pull_requests", scope={"repository": "asteria/platform"})
    service.run(
        tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id,
        connector=GitHubPullRequestConnector("https://api.github.test", github_transport()),
        request=request, idempotency_key="baseline-version",
    )
    transport = github_transport(mutate_same_version=True)
    with pytest.raises(SourceVersionConflictError):
        service.run(
            tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
            grant_id=grant.grant_id,
            connector=GitHubPullRequestConnector("https://api.github.test", transport),
            request=request, idempotency_key="conflicting-version",
        )
    failed = service.get_run("tnt_a", next(
        row.run_id for row in _runs(service) if row.idempotency_key == "conflicting-version"
    ))
    assert failed.status == "partial"


def _runs(service: ConnectorService):
    with service.database.read_session() as session:
        return list(session.scalars(select(ConnectorRun)))

def test_jira_enhanced_search_uses_next_page_token():
    base = "https://asteria.atlassian.test"
    transport = FixtureTransport({
        route("GET", f"{base}/rest/api/3/myself"): [response({"accountId": "acct-1"})],
        route("POST", f"{base}/rest/api/3/search/jql"): [
            response({"issues": [{"id": "10", "key": "CHANGE-10", "fields": {"updated": "2026-08-01T10:00:00.000+0000", "summary": "Deploy"}}], "nextPageToken": "next-1"}),
            response({"issues": [{"id": "11", "key": "CHANGE-11", "fields": {"updated": "2026-08-02T10:00:00.000+0000", "summary": "Deploy 2"}}]}),
        ],
    })
    connector = JiraIssueConnector(base, transport)
    assert connector.health().status == "healthy"
    pages = list(connector.collect_pages(
        CollectionRequest(stream="issues", scope={"projects": ["CHANGE"]}, parameters={"jql": "status = Done"}),
        {},
    ))
    assert [len(page.objects) for page in pages] == [1, 1]
    assert pages[-1].next_cursor == {"next_page_token": None}
    assert transport.requests[-1].json_body["nextPageToken"] == "next-1"
    assert "project in (CHANGE)" in transport.requests[1].json_body["jql"]


def test_confluence_cursor_pagination_and_space_scope():
    base = "https://asteria.atlassian.test"
    params1 = {"space-id": ["100"], "body-format": "storage", "limit": 100}
    params2 = {**params1, "cursor": "abc"}
    transport = FixtureTransport({
        route("GET", f"{base}/wiki/api/v2/spaces", {"limit": 1}): [response({"results": [{"id": "100"}]})],
        route("GET", f"{base}/wiki/api/v2/pages", params1): [response({"results": [{"id": "1", "spaceId": "100", "title": "Policy", "version": {"number": 1, "createdAt": "2026-08-01T10:00:00Z"}, "_links": {"webui": "/wiki/spaces/ENG/pages/1"}}], "_links": {"next": "/wiki/api/v2/pages?cursor=abc"}})],
        route("GET", f"{base}/wiki/api/v2/pages", params2): [response({"results": []})],
    })
    connector = ConfluencePageConnector(base, transport)
    assert connector.health().status == "healthy"
    pages = list(connector.collect_pages(CollectionRequest(stream="pages", scope={"space_ids": ["100"]}), {}))
    assert len(pages) == 2
    assert pages[0].next_cursor == {"cursor": "abc"}
    assert pages[0].objects[0].metadata["space_id"] == "100"


def test_google_drive_token_pagination_is_metadata_only():
    base = "https://www.googleapis.test"
    common = {
        "q": "trashed = false", "pageSize": 1000,
        "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,createdTime,version,md5Checksum,size,parents,driveId,webViewLink,trashed)",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "orderBy": "modifiedTime,name",
    }
    transport = FixtureTransport({
        route("GET", f"{base}/drive/v3/about", {"fields": "user"}): [response({"user": {"permissionId": "p1"}})],
        route("GET", f"{base}/drive/v3/files", common): [response({"files": [{"id": "f1", "name": "Policy", "version": "4", "modifiedTime": "2026-08-01T10:00:00Z", "webViewLink": "https://drive.test/f1"}], "nextPageToken": "tok"})],
        route("GET", f"{base}/drive/v3/files", {**common, "pageToken": "tok"}): [response({"files": []})],
    })
    connector = GoogleDriveFileConnector(base, transport)
    assert connector.health().status == "healthy"
    pages = list(connector.collect_pages(CollectionRequest(stream="files", scope={"drive_ids": ["my-drive"]}), {}))
    assert len(pages) == 2
    assert pages[0].objects[0].metadata["metadata_only"] is True
    assert pages[-1].next_cursor == {"page_token": None}


def test_google_drive_changes_uses_durable_start_tokens():
    base = "https://www.googleapis.test"
    start_params = {"supportsAllDrives": "true"}
    changes1 = {
        "pageToken": "start-1", "pageSize": 1000, "spaces": "drive",
        "includeRemoved": "true", "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "fields": "nextPageToken,newStartPageToken,changes(changeType,time,removed,fileId,driveId,file(id,name,mimeType,modifiedTime,createdTime,version,md5Checksum,size,parents,driveId,webViewLink,trashed))",
    }
    changes2 = {**changes1, "pageToken": "next-1"}
    transport = FixtureTransport({
        route("GET", f"{base}/drive/v3/changes/startPageToken", start_params): [response({"startPageToken": "start-1"})],
        route("GET", f"{base}/drive/v3/changes", changes1): [response({
            "changes": [{"changeType": "file", "time": "2026-08-01T10:00:00Z", "removed": False, "fileId": "f1", "file": {"id": "f1", "version": "2", "name": "Policy", "webViewLink": "https://drive.test/f1"}}],
            "nextPageToken": "next-1",
        })],
        route("GET", f"{base}/drive/v3/changes", changes2): [response({
            "changes": [{"changeType": "file", "time": "2026-08-02T10:00:00Z", "removed": True, "fileId": "f2"}],
            "newStartPageToken": "start-2",
        })],
    })
    connector = GoogleDriveFileConnector(base, transport)
    pages = list(connector.collect_pages(
        CollectionRequest(stream="changes", scope={"drive_ids": ["my-drive"]}), {}
    ))
    assert [page.next_cursor for page in pages] == [
        {"start_page_token": "next-1"}, {"start_page_token": "start-2"}
    ]
    assert pages[1].objects[0].metadata["removed"] is True
    assert pages[0].objects[0].metadata["incremental"] is True


def test_failed_run_resumes_from_page_checkpoint_without_duplicate_evidence(service: ConnectorService):
    from assuranceos.connectors import ConnectorDescriptor, ConnectorHealth, ConnectorPage, SourceObject

    instance = service.register_instance(
        "tnt_a",
        ConnectorInstanceInput(
            connector_key="fixture-resume", connector_type="fixture", display_name="Fixture Resume"
        ),
    )
    grant = service.create_grant(
        "tnt_a", instance.connector_instance_id,
        CollectionGrantInput(
            grant_key="resume", purpose="resume test", allowed_streams=["records"],
            resource_selectors={"datasets": ["scm"]}, approved_by="owner",
        ),
    )

    class ResumeConnector:
        descriptor = ConnectorDescriptor(
            connector_type="fixture", display_name="Fixture", streams=("records",)
        )
        def __init__(self, fail: bool): self.fail = fail
        def health(self):
            return ConnectorHealth(status="healthy", checked_at=datetime.now(timezone.utc))
        def scope_for(self, request): return {"datasets": "scm"}
        def collect_pages(self, request, checkpoint):
            page = int(checkpoint.get("next_page", 1))
            if page == 1:
                yield ConnectorPage(objects=[SourceObject(
                    source_object_id="1", source_version="1", source_locator="fixture://1", payload={"id": 1}
                )], next_cursor={"next_page": 2}, request_metadata={"page": 1})
                if self.fail:
                    raise RuntimeError("fixture interruption")
                page = 2
            if page == 2:
                yield ConnectorPage(objects=[SourceObject(
                    source_object_id="2", source_version="1", source_locator="fixture://2", payload={"id": 2}
                )], next_cursor={"next_page": 1}, request_metadata={"page": 2})

    request = CollectionRequest(stream="records", scope={"datasets": ["scm"]})
    with pytest.raises(RuntimeError, match="fixture interruption"):
        service.run(
            tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
            grant_id=grant.grant_id, connector=ResumeConnector(True), request=request,
            idempotency_key="resume-failed",
        )
    recovered = service.run(
        tenant_id="tnt_a", connector_instance_id=instance.connector_instance_id,
        grant_id=grant.grant_id, connector=ResumeConnector(False), request=request,
        idempotency_key="resume-success",
    )
    assert recovered.checkpoint_before == {"next_page": 2}
    assert recovered.objects_seen == 1
    assert recovered.objects_ingested == 1
    with service.database.read_session() as session:
        assert session.scalar(select(func.count(EvidenceRecord.evidence_id))) == 2

def test_pagination_and_rate_limit_helpers():
    links = parse_link_header('<https://api.test/items?page=2>; rel="next", <https://api.test/items?page=4>; rel="last"')
    assert links["next"].endswith("page=2")
    with pytest.raises(ConnectorRateLimitError) as caught:
        validate_response(response({"message": "slow down"}, status=429, headers={"Retry-After": "3"}))
    assert caught.value.retry_after_seconds == 3


def test_grant_expiry_requires_timezone():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="timezone"):
        CollectionGrantInput(
            grant_key="naive", purpose="invalid", allowed_streams=["issues"],
            approved_by="owner", expires_at=datetime(2026, 8, 7, 12, 0, 0),
        )

def test_static_credentials_are_not_exposed_in_repr():
    from assuranceos.connectors import StaticHeaderCredential

    credential = StaticHeaderCredential({"Authorization": "Bearer secret"})
    assert "secret" not in repr(credential)
    assert credential.headers()["Authorization"] == "Bearer secret"
