from __future__ import annotations

import pytest

from assuranceos.connectors import CollectionRequest, FixtureTransport, HttpResponse
from assuranceos.connectors.adapters import (
    EntraDirectoryConnector,
    GoogleCloudIamConnector,
    OktaDirectoryConnector,
)
from assuranceos.connectors.transport import normalized_url


def _route(method: str, url: str, params=None):
    return (method, normalized_url(url, params or {}))


def _response(body, *, headers=None):
    return HttpResponse(status_code=200, headers=headers or {}, json_body=body)


def test_okta_users_are_group_bounded_and_cursor_paginated():
    base = "https://asteria.okta.test"
    path = f"{base}/api/v1/groups/grp-1/users"
    transport = FixtureTransport(
        {
            _route("GET", path, {"limit": 200}): [
                _response(
                    [{"id": "u1", "lastUpdated": "2026-08-01T10:00:00Z"}],
                    headers={"Link": f'<{path}?after=cursor-1>; rel="next"'},
                )
            ],
            _route("GET", path, {"limit": 200, "after": "cursor-1"}): [
                _response([{"id": "u2", "lastUpdated": "2026-08-02T10:00:00Z"}])
            ],
        }
    )
    pages = list(
        OktaDirectoryConnector(base, transport).collect_pages(
            CollectionRequest(stream="users", scope={"group_ids": ["grp-1"]}), {}
        )
    )
    assert [page.objects[0].source_object_id for page in pages] == ["u1", "u2"]
    assert pages[0].next_cursor == {"group_index": 0, "after": "cursor-1"}
    assert all(page.objects[0].metadata["bounded_by_group"] == "grp-1" for page in pages)


def test_entra_group_members_follow_odata_next_link():
    base = "https://graph.microsoft.test"
    path = f"{base}/v1.0/groups/grp-1/transitiveMembers"
    next_url = f"{path}?$skiptoken=next"
    transport = FixtureTransport(
        {
            _route("GET", path, {"$top": 100}): [
                _response({"value": [{"id": "u1"}], "@odata.nextLink": next_url})
            ],
            _route("GET", next_url): [_response({"value": [{"id": "u2"}]})],
        }
    )
    pages = list(
        EntraDirectoryConnector(base, transport).collect_pages(
            CollectionRequest(stream="group_members", scope={"group_ids": ["grp-1"]}),
            {},
        )
    )
    assert [page.objects[0].source_object_id for page in pages] == ["u1", "u2"]
    assert pages[-1].next_cursor == {"object_index": 1, "next_url": None}


def test_gcp_iam_requests_version_three_policy_for_allowlisted_projects():
    base = "https://cloudresourcemanager.googleapis.test"
    transport = FixtureTransport(
        {
            _route("POST", f"{base}/v1/projects/proj-a:getIamPolicy"): [
                _response(
                    {
                        "version": 3,
                        "etag": "etag-a",
                        "bindings": [
                            {"role": "roles/viewer", "members": ["group:auditors@example.test"]}
                        ],
                    }
                )
            ]
        }
    )
    pages = list(
        GoogleCloudIamConnector(base, transport).collect_pages(
            CollectionRequest(stream="project_iam_policies", scope={"project_ids": ["proj-a"]}),
            {},
        )
    )
    assert pages[0].objects[0].source_version == "v3:etag-a"
    assert transport.requests[0].json_body == {"options": {"requestedPolicyVersion": 3}}
    assert pages[0].next_cursor == {"project_index": 1}


@pytest.mark.parametrize(
    ("connector", "collection_request"),
    [
        (
            OktaDirectoryConnector("https://okta.test", FixtureTransport({})),
            CollectionRequest(stream="users"),
        ),
        (
            EntraDirectoryConnector("https://graph.test", FixtureTransport({})),
            CollectionRequest(stream="users"),
        ),
        (
            GoogleCloudIamConnector("https://gcp.test", FixtureTransport({})),
            CollectionRequest(stream="project_iam_policies"),
        ),
    ],
)
def test_identity_connectors_refuse_unbounded_collection(connector, collection_request):
    with pytest.raises(ValueError, match="non-empty"):
        list(connector.collect_pages(collection_request, {}))
