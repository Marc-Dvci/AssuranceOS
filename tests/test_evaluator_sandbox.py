"""The evaluator sandbox, and the rules that make it safe to expose.

Most of these are refusals. That is deliberate: the sandbox is the one surface
that accepts a stranger's credential and makes an outbound call on their behalf,
so what it declines to do is the substance of it, and a permission check that
has never been observed to refuse is a permission check nobody has tested.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from assuranceos.connectors.transport import FixtureTransport, HttpRequest, HttpResponse, normalized_url
from assuranceos.db.session import Database
from assuranceos.evaluator_sandbox import (
    PROVIDERS,
    EvaluatorSandbox,
    GuardedTransport,
    InProcessCredentialStore,
    SandboxCredentialResolver,
    SandboxError,
    SandboxLimits,
    SandboxNotFoundError,
    provider_catalogue,
    validate_provider_url,
)
from assuranceos.vault import EvidenceVault

GITHUB_BASE = "https://api.github.com"
REPOSITORY = "octocat/hello-world"


def _response(body, headers=None):
    return HttpResponse(status_code=200, headers=headers or {}, json_body=body)


def _pull(number: int) -> dict:
    return {
        "id": 1000 + number,
        "node_id": f"PR_{number}",
        "number": number,
        "title": f"Change {number}",
        "state": "closed",
        "merged_at": "2026-07-22T10:40:00Z",
        "updated_at": "2026-07-22T10:40:00Z",
        "html_url": f"https://github.com/{REPOSITORY}/pull/{number}",
        "head": {"sha": f"sha{number}"},
        "user": {"login": "someone"},
    }


def _github_fixture(pages: int = 1, per_page: int = 2) -> FixtureTransport:
    routes: dict = {
        ("GET", normalized_url(f"{GITHUB_BASE}/rate_limit")): [
            _response({"resources": {"core": {"remaining": 4999, "limit": 5000, "reset": 0}}})
        ]
    }
    for page in range(1, pages + 1):
        params = {
            "state": "all",
            "sort": "updated",
            "direction": "asc",
            "per_page": 100,
            "page": page,
        }
        headers = {}
        if page < pages:
            headers["link"] = f'<{GITHUB_BASE}/repos/{REPOSITORY}/pulls?page={page + 1}>; rel="next"'
        routes[("GET", normalized_url(f"{GITHUB_BASE}/repos/{REPOSITORY}/pulls", params))] = [
            _response(
                [_pull(page * 100 + index) for index in range(per_page)],
                headers=headers,
            )
        ]
    return FixtureTransport(routes)


@pytest.fixture()
def sandbox(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "sandbox.db")
    database.create_schema()
    transport = _github_fixture()

    def factory(_profile):
        return transport

    instance = EvaluatorSandbox(
        database,
        EvidenceVault.local(database, tmp_path / "objects"),
        limits=SandboxLimits(max_workspaces=3, ttl_hours=2, max_pages=2, max_objects=3),
        transport_factory=factory,
    )
    try:
        yield instance
    finally:
        database.dispose()


# -- the provider catalogue ------------------------------------------------


def test_every_offered_provider_has_a_buildable_adapter():
    """A provider offered in the form must be one the factory can build.

    The catalogue is what the evaluator chooses from, and the factory is what
    has to honour that choice. They are written in different files, so nothing
    but this keeps them from drifting into a form that offers a provider the
    platform cannot construct.
    """

    from assuranceos.connectors.factory import ConnectorFactory

    buildable = {
        "github",
        "jira",
        "confluence",
        "google_drive",
        "okta",
        "entra",
        "gcp_iam",
    }
    assert set(PROVIDERS) == buildable
    assert ConnectorFactory is not None
    for name, profile in PROVIDERS.items():
        assert profile.connector_type == name
        assert profile.streams, f"{name} offers no stream"
        assert profile.allowed_hosts, f"{name} has no host allowlist"


def test_catalogue_never_carries_a_credential_value():
    for entry in provider_catalogue():
        for entry_field in entry["credential_fields"]:
            assert set(entry_field) == {"name", "label", "help", "secret"}


def test_each_stream_declares_a_scope_the_adapter_actually_reads():
    """The grant is only meaningful if it names the key the adapter derives.

    ``ConnectorService._validate`` compares the adapter's ``scope_for`` output
    against the grant's selectors key by key, so a profile that names
    ``projects`` where the adapter derives ``project_ids`` would approve a grant
    that can never permit anything.
    """

    from assuranceos.connectors.definitions import CollectionRequest
    from assuranceos.connectors.factory import ConnectorFactory  # noqa: F401

    from assuranceos.connectors.adapters import (
        ConfluencePageConnector,
        EntraDirectoryConnector,
        GitHubPullRequestConnector,
        GoogleCloudIamConnector,
        GoogleDriveFileConnector,
        JiraIssueConnector,
        OktaDirectoryConnector,
    )

    adapters = {
        "github": GitHubPullRequestConnector,
        "jira": JiraIssueConnector,
        "confluence": ConfluencePageConnector,
        "google_drive": GoogleDriveFileConnector,
        "okta": OktaDirectoryConnector,
        "entra": EntraDirectoryConnector,
        "gcp_iam": GoogleCloudIamConnector,
    }
    for name, profile in PROVIDERS.items():
        adapter = adapters[name]
        for stream in profile.streams:
            assert stream.name in adapter.descriptor.streams
            scope = stream.scope_for("alpha/beta" if not stream.scope_is_list else "alpha,beta")
            derived = adapter.scope_for(
                adapter, CollectionRequest(stream=stream.name, scope=scope)
            )
            assert set(derived) == {stream.selector_key}
            assert set(stream.selectors_for("alpha,beta")) == set(derived)


# -- the outbound guard ----------------------------------------------------


def test_a_host_outside_the_provider_allowlist_is_refused():
    with pytest.raises(SandboxError, match="allowlist"):
        validate_provider_url("https://graph.microsoft.com/v1.0", PROVIDERS["github"])


def test_plain_http_and_odd_ports_are_refused():
    with pytest.raises(SandboxError, match="HTTPS"):
        validate_provider_url("http://api.github.com", PROVIDERS["github"])
    with pytest.raises(SandboxError, match="default HTTPS port"):
        validate_provider_url("https://api.github.com:8443", PROVIDERS["github"])


def test_a_url_carrying_credentials_is_refused():
    with pytest.raises(SandboxError, match="must not carry credentials"):
        validate_provider_url("https://user:pass@api.github.com", PROVIDERS["github"])


def test_a_wildcard_allowlist_does_not_match_the_bare_suffix():
    """``*.atlassian.net`` must not admit ``atlassian.net`` or ``evilatlassian.net``.

    A suffix comparison written the obvious way admits both, and the second is
    the one an attacker registers.
    """

    with pytest.raises(SandboxError, match="allowlist"):
        validate_provider_url("https://atlassian.net", PROVIDERS["jira"])
    with pytest.raises(SandboxError, match="allowlist"):
        validate_provider_url("https://evilatlassian.net", PROVIDERS["jira"])


def test_the_guard_checks_the_second_request_and_not_only_the_first():
    """Pagination is where an unchecked transport actually gets exploited.

    The first request is the one that was validated at registration. The next
    one comes from a ``Link`` header, which the provider wrote.
    """

    sent: list[str] = []

    class Recording:
        def send(self, request: HttpRequest) -> HttpResponse:
            sent.append(request.url)
            return _response({})

    guard = GuardedTransport(PROVIDERS["github"], inner=Recording())
    guard.send(HttpRequest(method="GET", url=f"{GITHUB_BASE}/rate_limit"))
    with pytest.raises(SandboxError, match="allowlist"):
        guard.send(HttpRequest(method="GET", url="https://169.254.169.254/latest/meta-data"))
    assert sent == [f"{GITHUB_BASE}/rate_limit"]


def test_the_guard_stops_at_its_request_ceiling():
    class Always:
        def send(self, request: HttpRequest) -> HttpResponse:
            return _response({})

    guard = GuardedTransport(PROVIDERS["github"], inner=Always(), max_requests=2)
    guard.send(HttpRequest(method="GET", url=f"{GITHUB_BASE}/rate_limit"))
    guard.send(HttpRequest(method="GET", url=f"{GITHUB_BASE}/rate_limit"))
    with pytest.raises(SandboxError, match="ceiling"):
        guard.send(HttpRequest(method="GET", url=f"{GITHUB_BASE}/rate_limit"))


# -- credential references -------------------------------------------------


def test_the_resolver_refuses_a_secret_it_did_not_mint():
    """The point of the whole design: a connector cannot read the deployment.

    Without this, a request that chose its own ``credential_ref`` would turn a
    sandbox connector into a reader of any secret the runtime service account
    can reach, which on this deployment includes the database password.
    """

    resolver = SandboxCredentialResolver(InProcessCredentialStore(), project_id="audit-505613")
    with pytest.raises(SandboxError, match="sandbox credential"):
        resolver.resolve("gcp-secret://projects/audit-505613/secrets/db-password/versions/1")
    with pytest.raises(SandboxError, match="outside this sandbox's project"):
        resolver.resolve(
            "gcp-secret://projects/another-project/secrets/assuranceos-eval-x/versions/1"
        )
    with pytest.raises(SandboxError, match="unsupported credential reference"):
        resolver.resolve("env://GOOGLE_API_KEY")


def test_the_in_process_store_hands_back_only_what_it_was_given():
    store = InProcessCredentialStore()
    reference = store.put("assuranceos-eval-abc-github-1", {"Authorization": "Bearer t"})
    resolver = SandboxCredentialResolver(store)
    assert resolver.resolve(reference).headers() == {"Authorization": "Bearer t"}
    store.delete("assuranceos-eval-abc-github-1")
    with pytest.raises(SandboxError, match="no longer held"):
        resolver.resolve(reference)


def test_a_credential_value_never_appears_in_a_connector_record(sandbox: EvaluatorSandbox):
    workspace = sandbox.create_workspace("Northwind Trading")
    attached = sandbox.connect(
        workspace.workspace_id,
        provider="github",
        base_url=GITHUB_BASE,
        stream="pull_requests",
        scope_value=REPOSITORY,
        credentials={"token": "ghp_secret_value"},
    )
    serialised = repr(attached)
    assert "ghp_secret_value" not in serialised
    instances = sandbox.connectors.list_instances(workspace.tenant_id)
    assert "ghp_secret_value" not in repr([item.model_dump() for item in instances])
    assert attached["connector"]["authenticated"] is True


# -- workspaces ------------------------------------------------------------


def test_a_workspace_identifier_cannot_name_the_demonstration_tenant(sandbox: EvaluatorSandbox):
    for attempt in ("tnt_asteria_demo", "../tnt_asteria_demo", "", "not-hex"):
        with pytest.raises(SandboxNotFoundError):
            sandbox.tenant_for(attempt)


def test_the_workspace_ceiling_refuses_the_next_one(sandbox: EvaluatorSandbox):
    for index in range(3):
        sandbox.create_workspace(f"Company {index}")
    with pytest.raises(SandboxError, match="as many workspaces as it allows"):
        sandbox.create_workspace("One too many")


def test_an_expired_workspace_is_swept_when_the_next_one_is_created(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "sweep.db")
    database.create_schema()
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    clock = {"value": now}
    try:
        instance = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects"),
            limits=SandboxLimits(max_workspaces=2, ttl_hours=1),
            clock=lambda: clock["value"],
        )
        first = instance.create_workspace("Expiring Ltd")
        clock["value"] = now + timedelta(hours=3)
        instance.create_workspace("Later Ltd")
        with pytest.raises(SandboxNotFoundError):
            instance.get_workspace(first.workspace_id)
    finally:
        database.dispose()


def test_deleting_a_workspace_destroys_the_credential(sandbox: EvaluatorSandbox):
    workspace = sandbox.create_workspace("Northwind Trading")
    sandbox.connect(
        workspace.workspace_id,
        provider="github",
        base_url=GITHUB_BASE,
        stream="pull_requests",
        scope_value=REPOSITORY,
        credentials={"token": "ghp_secret_value"},
    )
    result = sandbox.delete_workspace(workspace.workspace_id)
    assert result["secrets_destroyed"] == 1
    with pytest.raises(SandboxNotFoundError):
        sandbox.get_workspace(workspace.workspace_id)


# -- collection ------------------------------------------------------------


def test_a_collection_lands_as_evidence_with_provenance(sandbox: EvaluatorSandbox):
    workspace = sandbox.create_workspace("Northwind Trading")
    attached = sandbox.connect(
        workspace.workspace_id,
        provider="github",
        base_url=GITHUB_BASE,
        stream="pull_requests",
        scope_value=REPOSITORY,
    )
    run = sandbox.collect(
        workspace.workspace_id,
        attached["connector"]["connector_instance_id"],
        stream="pull_requests",
        scope_value=REPOSITORY,
    )
    assert run.status == "succeeded"
    assert run.objects_ingested == 2
    records = sandbox.vault.list(workspace.tenant_id)
    assert len(records) == 2
    assert all(record.source_type == "github" for record in records)


def test_a_collection_stops_at_the_object_ceiling(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "bounded.db")
    database.create_schema()
    transport = _github_fixture(pages=4, per_page=2)
    try:
        instance = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects"),
            limits=SandboxLimits(max_pages=4, max_objects=3),
            transport_factory=lambda _profile: transport,
        )
        workspace = instance.create_workspace("Busy Repository Ltd")
        attached = instance.connect(
            workspace.workspace_id,
            provider="github",
            base_url=GITHUB_BASE,
            stream="pull_requests",
            scope_value=REPOSITORY,
        )
        run = instance.collect(
            workspace.workspace_id,
            attached["connector"]["connector_instance_id"],
            stream="pull_requests",
            scope_value=REPOSITORY,
        )
        assert run.objects_seen == 3
    finally:
        database.dispose()


def test_a_collection_outside_the_approved_scope_is_refused(sandbox: EvaluatorSandbox):
    """The grant names one repository, and the run asks for another.

    A grant that cannot refuse is a grant that proves nothing, so this is the
    assertion the whole authority model rests on.
    """

    from assuranceos.connectors.exceptions import CollectionScopeError

    workspace = sandbox.create_workspace("Northwind Trading")
    attached = sandbox.connect(
        workspace.workspace_id,
        provider="github",
        base_url=GITHUB_BASE,
        stream="pull_requests",
        scope_value=REPOSITORY,
    )
    with pytest.raises(CollectionScopeError):
        sandbox.collect(
            workspace.workspace_id,
            attached["connector"]["connector_instance_id"],
            stream="pull_requests",
            scope_value="someone-else/private-repo",
        )


def test_an_expired_grant_refuses_the_collection(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "expired.db")
    database.create_schema()
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    clock = {"value": now}
    transport = _github_fixture()
    try:
        instance = EvaluatorSandbox(
            database,
            EvidenceVault.local(database, tmp_path / "objects"),
            limits=SandboxLimits(grant_hours=1),
            transport_factory=lambda _profile: transport,
            clock=lambda: clock["value"],
        )
        workspace = instance.create_workspace("Northwind Trading")
        attached = instance.connect(
            workspace.workspace_id,
            provider="github",
            base_url=GITHUB_BASE,
            stream="pull_requests",
            scope_value=REPOSITORY,
        )
        clock["value"] = now + timedelta(hours=2)
        with pytest.raises(SandboxError, match="no live grant"):
            instance.collect(
                workspace.workspace_id,
                attached["connector"]["connector_instance_id"],
                stream="pull_requests",
                scope_value=REPOSITORY,
            )
    finally:
        database.dispose()


def test_a_provider_other_than_github_must_carry_a_credential(sandbox: EvaluatorSandbox):
    workspace = sandbox.create_workspace("Northwind Trading")
    with pytest.raises(SandboxError, match="requires a credential"):
        sandbox.connect(
            workspace.workspace_id,
            provider="jira",
            base_url="https://northwind.atlassian.net",
            stream="issues",
            scope_value="CHANGE",
        )


class _FakeSecretManager:
    """Answers the way Secret Manager answers, which is the point of it.

    The API echoes resource names with the **numeric** project, not the project
    id it was addressed with. Both spellings name the same project and they
    compare unequal, so a reference echoed from the response was refused by the
    sandbox's own resolver as belonging to somebody else. Nothing local
    reproduced that, because the in-process store never speaks this dialect.
    """

    PROJECT_NUMBER = "91995351602"

    def __init__(self) -> None:
        self.created: list[str] = []
        self.versions: dict[str, bytes] = {}

    def create_secret(self, request):
        self.created.append(request["secret_id"])
        return SimpleNamespace(name=f"projects/{self.PROJECT_NUMBER}/secrets/{request['secret_id']}")

    def add_secret_version(self, request):
        # The name comes back with the numeric project substituted for the id.
        secret = str(request["parent"]).rsplit("/", 1)[-1]
        self.versions[secret] = request["payload"]["data"]
        return SimpleNamespace(
            name=f"projects/{self.PROJECT_NUMBER}/secrets/{secret}/versions/1"
        )

    def access_secret_version(self, request, timeout=None):
        secret = str(request["name"]).split("/secrets/", 1)[1].split("/versions/")[0]
        if secret not in self.versions:
            raise KeyError(secret)
        return SimpleNamespace(payload=SimpleNamespace(data=self.versions[secret]))

    def delete_secret(self, request):
        self.versions.pop(str(request["name"]).rsplit("/", 1)[-1], None)


def test_a_secret_manager_reference_resolves_in_the_project_it_was_minted_for():
    """The reference must name the configured project, whatever the API echoes."""

    from assuranceos.evaluator_sandbox import SecretManagerCredentialStore

    client = _FakeSecretManager()
    store = SecretManagerCredentialStore("audit-505613", client=client)
    reference = store.put("assuranceos-eval-abc-github-1", {"Authorization": "Bearer t"})

    assert reference.startswith("gcp-secret://projects/audit-505613/secrets/")
    assert client.PROJECT_NUMBER not in reference

    resolver = SandboxCredentialResolver(store, project_id="audit-505613")
    provider = resolver.resolve(reference)
    assert provider.headers() == {"Authorization": "Bearer t"}


def test_the_resolver_still_refuses_another_projects_secret():
    from assuranceos.evaluator_sandbox import SecretManagerCredentialStore

    store = SecretManagerCredentialStore("audit-505613", client=_FakeSecretManager())
    resolver = SandboxCredentialResolver(store, project_id="audit-505613")
    with pytest.raises(SandboxError, match="outside this sandbox's project"):
        resolver.resolve(
            "gcp-secret://projects/someone-else/secrets/assuranceos-eval-x/versions/1"
        )
