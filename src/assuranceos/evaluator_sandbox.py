"""A workspace an evaluator can point at their own systems.

The demonstration tenant is a company that does not exist, and a company that
does not exist cannot answer the only question anybody really has about a
connector: does it work on mine. This module is the answer. Whoever holds the
evaluator credential gets a tenant of their own, attaches their own provider
account to it, and the platform runs the same governed collection path it runs
for the demonstration -- the same grant check, the same evidence vault, the same
provenance on every object -- over their data instead of invented data.

Four properties are load-bearing, because this is a public service that accepts
other people's credentials:

* **The caller never names a credential reference.** The connector design stores
  references and never values, which is only a control if the reference cannot
  be chosen by the caller. A request that could supply
  ``gcp-secret://.../some-other-secret/1`` would turn a connector into a reader
  of the deployment's own secrets. So the workspace mints the reference itself,
  from the workspace id, and :class:`SandboxCredentialResolver` refuses to
  resolve anything that does not carry the prefix it minted.

* **Every outbound request is checked, not just the first one.** Adapters follow
  provider pagination by absolute URL, so validating the base URL at
  registration would leave the second page unchecked -- and a ``Link`` header
  pointing at ``169.254.169.254`` is exactly how a metadata endpoint gets read
  with somebody's credential attached. :class:`GuardedTransport` sits under the
  adapter and validates every request it makes, against the allowlist for that
  provider and against the resolved addresses.

* **A collection is bounded.** The product's own collection runs to the end of
  the stream, which is correct when an audit owner asked for a population and
  wrong when the caller is a stranger with a large repository. Runs here carry a
  page and object ceiling and stop on it, and the run says that it stopped.

* **A workspace is disposable, and says when it expires.** It holds a stranger's
  credential; the shortest life that still allows the demonstration is the right
  one. Deleting a workspace revokes the grants, destroys the secrets, and drops
  the tenant with it.

Nothing here can reach the demonstration tenant. The routes take a workspace id,
not a tenant id, and the tenant is derived from the workspace -- so a caller who
holds the evaluator credential can build in their own workspace and still cannot
write one row into the company the demonstration runs on.
"""

from __future__ import annotations

import re
import secrets
from base64 import b64encode
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlsplit

from sqlalchemy import func, select

from .connectors.credentials import (
    CredentialProvider,
    CredentialResolver,
    StaticHeaderCredential,
)
from .connectors.definitions import (
    CollectionGrantInput,
    CollectionRequest,
    ConnectorInstanceInput,
    ConnectorInstanceView,
    ConnectorRunSummary,
)
from .connectors.factory import ConnectorFactory
from .connectors.service import ConnectorService
from .connectors.transport import HttpRequest, HttpResponse, HttpTransport, HttpxTransport
from .db.base import as_utc
from .db.models import Tenant
from .db.repositories import TenantRepository
from .db.session import Database
from .public_sources import (  # the address rules are the same rules, and one copy of them is the point
    _addresses,
    _refuse_internal,
)
from .vault import EvidenceVault

WORKSPACE_PREFIX = "tnt_eval_"
SECRET_NAME_PREFIX = "assuranceos-eval-"
MEMORY_SCHEME = "sandbox-memory://"


class SandboxError(RuntimeError):
    """A sandbox request was refused. The reason is always a rule stated here."""


class SandboxNotFoundError(SandboxError):
    """No workspace with that identifier, or it has expired and been swept."""


# --------------------------------------------------------------------------
# Provider catalogue
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialField:
    """One value the evaluator supplies, and what it is for.

    ``secret`` drives redaction rather than presentation: a field marked secret
    is never echoed back by any endpoint, never written to an audit event, and
    never stored anywhere but the credential store.
    """

    name: str
    label: str
    help: str
    secret: bool = True


@dataclass(frozen=True)
class StreamProfile:
    """A stream on a provider, the scope a request carries, and the scope a grant names.

    Those last two are not always the same word, and assuming they were is a
    real defect this shape prevents. A GitHub collection request carries
    ``repository`` as one string; the adapter's ``scope_for`` derives
    ``repositories``, and it is *that* key the grant's selectors are compared
    against. A profile that named only one of them would approve a grant which
    could never permit anything, and the failure would arrive as a scope refusal
    on a correctly configured connector.
    """

    name: str
    label: str
    scope_key: str
    scope_label: str
    scope_help: str
    scope_is_list: bool = True
    grant_key: str | None = None

    @property
    def selector_key(self) -> str:
        return self.grant_key or self.scope_key

    def _items(self, value: str) -> list[str]:
        items = [item.strip() for item in (value or "").split(",") if item.strip()]
        if not items:
            raise SandboxError(f"{self.scope_label} is required")
        return items

    def scope_for(self, value: str) -> dict[str, Any]:
        items = self._items(value)
        if self.scope_is_list:
            return {self.scope_key: items}
        if len(items) != 1:
            raise SandboxError(f"{self.scope_label} takes a single value")
        return {self.scope_key: items[0]}

    def selectors_for(self, value: str) -> dict[str, Any]:
        """What the grant approves, always as a list the request is checked against."""

        return {self.selector_key: self._items(value)}


def _bearer(field_name: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def build(values: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {values[field_name]}"}

    return build


def _basic(user_field: str, token_field: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def build(values: dict[str, str]) -> dict[str, str]:
        pair = f"{values[user_field]}:{values[token_field]}".encode("utf-8")
        return {"Authorization": f"Basic {b64encode(pair).decode('ascii')}"}

    return build


def _ssws(field_name: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def build(values: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"SSWS {values[field_name]}"}

    return build


@dataclass(frozen=True)
class ProviderProfile:
    """Everything the platform needs to let a stranger attach this provider.

    ``allowed_hosts`` is the list this provider's traffic may reach, and it is
    per provider rather than global because that is what makes it meaningful: a
    GitHub connector that may also reach ``graph.microsoft.com`` has an
    allowlist in name only.
    """

    connector_type: str
    display_name: str
    summary: str
    base_url_example: str
    allowed_hosts: tuple[str, ...]
    credential_fields: tuple[CredentialField, ...]
    header_builder: Callable[[dict[str, str]], dict[str, str]]
    streams: tuple[StreamProfile, ...]
    read_scopes: tuple[str, ...]
    documentation_url: str
    credential_help: str

    def stream(self, name: str) -> StreamProfile:
        for item in self.streams:
            if item.name == name:
                return item
        raise SandboxError(f"{self.display_name} does not expose a {name!r} stream")

    def headers(self, values: dict[str, str]) -> dict[str, str]:
        missing = [f.name for f in self.credential_fields if not values.get(f.name)]
        if missing:
            raise SandboxError(f"missing credential values: {', '.join(sorted(missing))}")
        return self.header_builder(values)

    def as_catalogue_entry(self) -> dict[str, Any]:
        return {
            "connector_type": self.connector_type,
            "display_name": self.display_name,
            "summary": self.summary,
            "base_url_example": self.base_url_example,
            "allowed_hosts": list(self.allowed_hosts),
            "credential_help": self.credential_help,
            "credential_fields": [
                {"name": f.name, "label": f.label, "help": f.help, "secret": f.secret}
                for f in self.credential_fields
            ],
            "streams": [
                {
                    "name": s.name,
                    "label": s.label,
                    "scope_key": s.scope_key,
                    "scope_label": s.scope_label,
                    "scope_help": s.scope_help,
                    "scope_is_list": s.scope_is_list,
                }
                for s in self.streams
            ],
            "read_scopes": list(self.read_scopes),
            "documentation_url": self.documentation_url,
        }


PROVIDERS: dict[str, ProviderProfile] = {
    "github": ProviderProfile(
        connector_type="github",
        display_name="GitHub",
        summary="Pull requests on a repository, as the change population an SCM control is tested over.",
        base_url_example="https://api.github.com",
        allowed_hosts=("api.github.com", "*.ghe.com"),
        credential_fields=(
            CredentialField(
                name="token",
                label="Personal access token",
                help="Fine-grained token, read-only, Pull requests: read. A public repository also reads without one.",
            ),
        ),
        header_builder=_bearer("token"),
        streams=(
            StreamProfile(
                name="pull_requests",
                label="Pull requests",
                scope_key="repository",
                scope_label="Repository",
                scope_help="owner/repository",
                scope_is_list=False,
                grant_key="repositories",
            ),
            StreamProfile(
                name="commits",
                label="Commits on the default branch",
                scope_key="repository",
                scope_label="Repository",
                scope_help="owner/repository",
                scope_is_list=False,
                grant_key="repositories",
            ),
            StreamProfile(
                name="commit_reviews",
                label="The review path of each commit",
                scope_key="repository",
                scope_label="Repository",
                scope_help="owner/repository",
                scope_is_list=False,
                grant_key="repositories",
            ),
        ),
        read_scopes=("Contents: read", "Pull requests: read"),
        documentation_url="https://docs.github.com/en/rest/pulls/pulls",
        credential_help="Leave the token empty to read a public repository unauthenticated, at sixty requests an hour.",
    ),
    "jira": ProviderProfile(
        connector_type="jira",
        display_name="Jira Cloud",
        summary="Issues in named projects, as the ticket population behind a change or remediation control.",
        base_url_example="https://your-site.atlassian.net",
        allowed_hosts=("*.atlassian.net",),
        credential_fields=(
            CredentialField(name="email", label="Account email", help="The Atlassian account the token belongs to.", secret=False),
            CredentialField(name="token", label="API token", help="Created at id.atlassian.com, scoped read:jira-work."),
        ),
        header_builder=_basic("email", "token"),
        streams=(
            StreamProfile(
                name="issues",
                label="Issues",
                scope_key="projects",
                scope_label="Project keys",
                scope_help="One or more project keys, comma separated.",
            ),
        ),
        read_scopes=("read:jira-work",),
        documentation_url="https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/",
        credential_help="Basic authentication over the account email and an API token, which is how Jira Cloud authenticates a read.",
    ),
    "confluence": ProviderProfile(
        connector_type="confluence",
        display_name="Confluence Cloud",
        summary="Pages in a space, as the stated procedure a control is measured against.",
        base_url_example="https://your-site.atlassian.net",
        allowed_hosts=("*.atlassian.net",),
        credential_fields=(
            CredentialField(name="email", label="Account email", help="The Atlassian account the token belongs to.", secret=False),
            CredentialField(name="token", label="API token", help="Created at id.atlassian.com, scoped read:page:confluence."),
        ),
        header_builder=_basic("email", "token"),
        streams=(
            StreamProfile(
                name="pages",
                label="Pages",
                scope_key="space_ids",
                scope_label="Space ids",
                scope_help="Numeric space ids, comma separated.",
            ),
        ),
        read_scopes=("read:page:confluence",),
        documentation_url="https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/",
        credential_help="The same credential as Jira. A site that uses both attaches it twice, once per connector, so each grant stays separate.",
    ),
    "google_drive": ProviderProfile(
        connector_type="google_drive",
        display_name="Google Drive",
        summary="File metadata in a drive, as the document population behind an evidence completeness check.",
        base_url_example="https://www.googleapis.com",
        allowed_hosts=("www.googleapis.com",),
        credential_fields=(
            CredentialField(
                name="access_token",
                label="OAuth access token",
                help="An access token with drive.metadata.readonly. Short-lived by design.",
            ),
        ),
        header_builder=_bearer("access_token"),
        streams=(
            StreamProfile(
                name="files",
                label="Files",
                scope_key="drive_ids",
                scope_label="Drive ids",
                scope_help="my-drive, or shared drive ids, comma separated.",
            ),
        ),
        read_scopes=("https://www.googleapis.com/auth/drive.metadata.readonly",),
        documentation_url="https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list",
        credential_help="Only metadata is read. The connector has no scope to open a file's contents.",
    ),
    "okta": ProviderProfile(
        connector_type="okta",
        display_name="Okta",
        summary="Group membership, as the population an access review reconciles against.",
        base_url_example="https://your-org.okta.com",
        allowed_hosts=("*.okta.com", "*.oktapreview.com", "*.okta-emea.com"),
        credential_fields=(
            CredentialField(name="token", label="API token", help="An Okta API token with okta.groups.read and okta.users.read."),
        ),
        header_builder=_ssws("token"),
        streams=(
            StreamProfile(
                name="groups",
                label="Groups",
                scope_key="group_ids",
                scope_label="Group ids",
                scope_help="Okta group ids, comma separated.",
            ),
            StreamProfile(
                name="users",
                label="Group members",
                scope_key="group_ids",
                scope_label="Group ids",
                scope_help="Okta group ids, comma separated.",
            ),
        ),
        read_scopes=("okta.groups.read", "okta.users.read"),
        documentation_url="https://developer.okta.com/docs/api/",
        credential_help="Okta authenticates management reads with its own SSWS scheme rather than a bearer token.",
    ),
    "entra": ProviderProfile(
        connector_type="entra",
        display_name="Microsoft Entra ID",
        summary="Directory users, groups and role assignments, as the identity population behind an access control.",
        base_url_example="https://graph.microsoft.com",
        allowed_hosts=("graph.microsoft.com",),
        credential_fields=(
            CredentialField(
                name="access_token",
                label="Graph access token",
                help="An application token holding User.Read.All or Group.Read.All.",
            ),
        ),
        header_builder=_bearer("access_token"),
        streams=(
            StreamProfile(
                name="groups",
                label="Groups",
                scope_key="group_ids",
                scope_label="Group object ids",
                scope_help="Directory group object ids, comma separated.",
            ),
            StreamProfile(
                name="group_members",
                label="Group members",
                scope_key="group_ids",
                scope_label="Group object ids",
                scope_help="Directory group object ids, comma separated.",
            ),
            StreamProfile(
                name="users",
                label="Users",
                scope_key="object_ids",
                scope_label="User object ids",
                scope_help="Directory user object ids, comma separated.",
            ),
        ),
        read_scopes=("User.Read.All", "Group.Read.All", "GroupMember.Read.All"),
        documentation_url="https://learn.microsoft.com/graph/api/resources/azure-ad-overview",
        credential_help="Graph tokens are short-lived, so a workspace usually outlives the token it was given.",
    ),
    "gcp_iam": ProviderProfile(
        connector_type="gcp_iam",
        display_name="Google Cloud IAM",
        summary="Project IAM policy bindings, as the privileged-access population behind a least-privilege control.",
        base_url_example="https://cloudresourcemanager.googleapis.com",
        allowed_hosts=("cloudresourcemanager.googleapis.com",),
        credential_fields=(
            CredentialField(
                name="access_token",
                label="Access token",
                help="From `gcloud auth print-access-token`, on an identity with cloud-platform.read-only.",
            ),
        ),
        header_builder=_bearer("access_token"),
        streams=(
            StreamProfile(
                name="project_iam_policies",
                label="Project IAM policies",
                scope_key="project_ids",
                scope_label="Project ids",
                scope_help="Google Cloud project ids, comma separated.",
            ),
        ),
        read_scopes=("https://www.googleapis.com/auth/cloud-platform.read-only",),
        documentation_url="https://cloud.google.com/resource-manager/reference/rest/v1/projects/getIamPolicy",
        credential_help="getIamPolicy is a read. The adapter exposes no binding write and the grant would refuse one.",
    ),
}


def provider_catalogue() -> list[dict[str, Any]]:
    return [PROVIDERS[key].as_catalogue_entry() for key in sorted(PROVIDERS)]


# --------------------------------------------------------------------------
# Outbound guard
# --------------------------------------------------------------------------


def _host_permitted(host: str, patterns: tuple[str, ...]) -> bool:
    candidate = host.lower().rstrip(".")
    for pattern in patterns:
        value = pattern.lower().rstrip(".")
        if value.startswith("*."):
            suffix = value[1:]
            if candidate.endswith(suffix) and len(candidate) > len(suffix):
                return True
        elif candidate == value:
            return True
    return False


def validate_provider_url(url: str, profile: ProviderProfile) -> str:
    """Refuse a URL before anything connects to it, and say which rule refused.

    The checks are the ones :mod:`assuranceos.public_sources` already applies to
    a public page, plus the provider's own allowlist. Resolution happens here
    too: a name that resolves to a private address is refused before a socket is
    opened, and *every* address it resolves to is checked, because which one a
    connection uses is not this process's decision.
    """

    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise SandboxError("a provider must be reached over HTTPS")
    if parsed.username or parsed.password:
        raise SandboxError("a provider URL must not carry credentials")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SandboxError("the provider URL has no host")
    if parsed.port not in (None, 443):
        raise SandboxError("a provider is read on the default HTTPS port only")
    if not _host_permitted(host, profile.allowed_hosts):
        allowed = ", ".join(profile.allowed_hosts)
        raise SandboxError(f"{host} is outside the {profile.display_name} allowlist ({allowed})")
    _refuse_internal(_addresses(host), host=host)
    return host


@dataclass
class GuardedTransport:
    """Validates every request an adapter makes, including the ones it derives.

    An adapter follows provider pagination by absolute URL. Checking only the
    registered base URL would therefore check the first request and none of the
    rest, and a ``Link`` header is under the provider's control rather than
    ours. Sitting under the adapter is the only position from which the check
    covers what the adapter actually does.

    The request ceiling is here for the same reason: it bounds the work a single
    call can start, wherever in the adapter the loop happens to live.
    """

    profile: ProviderProfile
    inner: HttpTransport = field(default_factory=HttpxTransport)
    max_requests: int = 60
    requests_made: int = 0

    def send(self, request: HttpRequest) -> HttpResponse:
        if self.requests_made >= self.max_requests:
            raise SandboxError(
                f"this collection reached its {self.max_requests}-request ceiling"
            )
        self.requests_made += 1
        validate_provider_url(request.url, self.profile)
        return self.inner.send(request)


# --------------------------------------------------------------------------
# Credential storage
# --------------------------------------------------------------------------


class SandboxCredentialStore:
    """Where a supplied credential goes, and the reference that comes back."""

    def put(self, secret_name: str, headers: dict[str, str]) -> str:  # pragma: no cover - protocol
        raise NotImplementedError

    def delete(self, secret_name: str) -> None:  # pragma: no cover - protocol
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - protocol
        raise NotImplementedError


class InProcessCredentialStore(SandboxCredentialStore):
    """Holds the credential in memory for the life of the process.

    This is the local and test store. It is not the deployed one: Cloud Run
    scales to zero, so a workspace whose credential lives in a process would
    stop working between two clicks with no explanation the evaluator could act
    on. It exists so the whole path can be tested without a cloud project.
    """

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}
        self._lock = Lock()

    def put(self, secret_name: str, headers: dict[str, str]) -> str:
        with self._lock:
            self._values[secret_name] = dict(headers)
        return f"{MEMORY_SCHEME}{secret_name}"

    def delete(self, secret_name: str) -> None:
        with self._lock:
            self._values.pop(secret_name, None)

    def resolve(self, secret_name: str) -> dict[str, str]:
        with self._lock:
            if secret_name not in self._values:
                raise SandboxError(
                    "this workspace's credential is no longer held by the running instance; "
                    "attach the connector again"
                )
            return dict(self._values[secret_name])

    def describe(self) -> str:
        return "in-process, for the life of this instance"


class SecretManagerCredentialStore(SandboxCredentialStore):
    """Writes the credential to Google Secret Manager and keeps the reference.

    The value never touches the canonical database, which is the invariant the
    whole connector design is built on, and it survives the instance that
    received it, which is what makes a workspace usable across a cold start.
    Deleting the workspace deletes the secret; the label is there so a sweep can
    find anything a delete missed.
    """

    def __init__(self, project_id: str, *, client: Any | None = None):
        if not project_id:
            raise SandboxError("a Secret Manager project is required")
        self.project_id = project_id
        self._client = client

    def secret_client(self) -> Any:
        """The API client, built once and shared with whatever resolves a reference."""

        if self._client is None:
            try:
                from google.cloud import secretmanager
            except ImportError as exc:  # pragma: no cover - optional cloud dependency
                raise SandboxError("install the cloud extra to use Secret Manager") from exc
            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    #: Kept as the old private name so the rest of this class reads unchanged.
    _api = secret_client

    def put(self, secret_name: str, headers: dict[str, str]) -> str:
        import json

        client = self._api()
        parent = f"projects/{self.project_id}"
        try:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_name,
                    "secret": {
                        "replication": {"automatic": {}},
                        "labels": {"assuranceos-sandbox": "true"},
                    },
                }
            )
        except Exception as exc:  # already-exists is the only benign case
            if "AlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc):
                raise SandboxError(f"could not create the credential secret: {exc}") from exc
        version = client.add_secret_version(
            request={
                "parent": f"{parent}/secrets/{secret_name}",
                "payload": {"data": json.dumps(headers).encode("utf-8")},
            }
        )
        # Built from the project this store was configured with, rather than
        # echoed from the response. Secret Manager answers with the *numeric*
        # project, and the two spellings address the same project while comparing
        # unequal -- so echoing the response produced a reference this sandbox
        # then refused to resolve as belonging to somebody else's project. Only
        # the version number is taken from the reply, because it is the only part
        # the caller did not already know.
        return (
            f"gcp-secret://projects/{self.project_id}/secrets/{secret_name}"
            f"/versions/{str(version.name).rsplit('/', 1)[-1]}"
        )

    def delete(self, secret_name: str) -> None:
        client = self._api()
        try:
            client.delete_secret(
                request={"name": f"projects/{self.project_id}/secrets/{secret_name}"}
            )
        except Exception:  # a workspace delete must not fail on an already-gone secret
            return

    def describe(self) -> str:
        return f"Google Secret Manager in {self.project_id}"


class SandboxCredentialResolver:
    """Resolves only the references this sandbox minted.

    Without this the sandbox would hand a caller-influenced string to the
    general resolver, and the general resolver's job is to resolve any valid
    reference -- including the deployment's own secrets. The prefix check is
    what keeps a connector a reader of the evaluator's provider and nothing
    else.
    """

    def __init__(self, store: SandboxCredentialStore, *, project_id: str | None = None):
        self.store = store
        self.project_id = project_id
        # The store's own client, rather than a second one. Building a fresh
        # client here would also mean a test that hands the store a fake watches
        # the resolver reach past it for the real API.
        self._delegate = CredentialResolver(
            secret_manager_client=(
                store.secret_client() if isinstance(store, SecretManagerCredentialStore) else None
            )
        )

    def resolve(self, reference: str | None) -> CredentialProvider:
        if reference is None:
            return StaticHeaderCredential({})
        if reference.startswith(MEMORY_SCHEME):
            name = reference.removeprefix(MEMORY_SCHEME)
            self._require_sandbox_name(name)
            assert isinstance(self.store, InProcessCredentialStore)
            return StaticHeaderCredential(self.store.resolve(name))
        if reference.startswith("gcp-secret://"):
            resource = reference.removeprefix("gcp-secret://")
            match = re.fullmatch(
                r"projects/([^/]+)/secrets/([^/]+)/versions/([^/]+)", resource
            )
            if match is None:
                raise SandboxError("the sandbox mints full Secret Manager resource names")
            project, name, _ = match.groups()
            if self.project_id and project != self.project_id:
                raise SandboxError("that secret is outside this sandbox's project")
            self._require_sandbox_name(name)
            return self._delegate.resolve(reference)
        raise SandboxError("unsupported credential reference for a sandbox connector")

    @staticmethod
    def _require_sandbox_name(name: str) -> None:
        if not name.startswith(SECRET_NAME_PREFIX):
            raise SandboxError("a sandbox connector may only read a sandbox credential")


# --------------------------------------------------------------------------
# The sandbox itself
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)




_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str, fallback: str) -> str:
    slug = _SLUG_SAFE.sub("-", value.strip().lower()).strip("-")
    return slug[:40] or fallback


@dataclass(frozen=True)
class WorkspaceView:
    workspace_id: str
    tenant_id: str
    company_name: str
    primary_domain: str | None
    created_at: datetime
    expires_at: datetime
    credential_storage: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "tenant_id": self.tenant_id,
            "company_name": self.company_name,
            "primary_domain": self.primary_domain,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "credential_storage": self.credential_storage,
        }


@dataclass
class SandboxLimits:
    """Ceilings a stranger's request runs inside.

    These are not tuning parameters. Every one of them is a thing an unbounded
    version of this endpoint would let an anonymous caller do to the deployment
    that hosts it.
    """

    max_workspaces: int = 25
    ttl_hours: int = 48
    max_pages: int = 5
    max_objects: int = 500
    max_requests: int = 60
    grant_hours: int = 6
    # An audit collects the review path of every commit, which is one request
    # per commit and one per distinct pull request. Held apart from
    # `max_requests` so that raising the ceiling for a deliberate, bounded audit
    # does not quietly raise it for every ordinary collection.
    audit_max_requests: int = 400
    audit_max_lookups: int = 150
    audit_max_commits: int = 300
    audit_period_days: int = 30


class EvaluatorSandbox:
    """Creates disposable workspaces and runs governed collections inside them."""

    def __init__(
        self,
        database: Database,
        vault: EvidenceVault,
        *,
        store: SandboxCredentialStore | None = None,
        limits: SandboxLimits | None = None,
        project_id: str | None = None,
        transport_factory: Callable[[ProviderProfile], HttpTransport] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.database = database
        self.vault = vault
        self.store = store or InProcessCredentialStore()
        self.limits = limits or SandboxLimits()
        self.project_id = project_id
        self.clock = clock
        self.connectors = ConnectorService(database, vault, clock=clock)
        self.resolver = SandboxCredentialResolver(self.store, project_id=project_id)
        self._transport_factory = transport_factory or self._guarded_transport

    def _guarded_transport(self, profile: ProviderProfile) -> HttpTransport:
        return GuardedTransport(profile, max_requests=self.limits.max_requests)

    # -- workspaces --------------------------------------------------------

    def create_workspace(self, company_name: str, primary_domain: str | None = None) -> WorkspaceView:
        name = company_name.strip()
        if not name:
            raise SandboxError("a company name is required")
        if len(name) > 120:
            raise SandboxError("a company name is at most 120 characters")
        self._sweep_expired()
        now = self.clock()
        with self.database.read_session() as session:
            live = int(
                session.scalar(
                    select(func.count(Tenant.tenant_id)).where(
                        Tenant.tenant_id.like(f"{WORKSPACE_PREFIX}%")
                    )
                )
                or 0
            )
        if live >= self.limits.max_workspaces:
            raise SandboxError(
                "the evaluation sandbox is holding as many workspaces as it allows; "
                "delete one, or try again once an existing workspace expires"
            )
        workspace_id = secrets.token_hex(16)
        tenant_id = f"{WORKSPACE_PREFIX}{workspace_id}"
        with self.database.transaction() as session:
            TenantRepository(session).add(
                Tenant(
                    tenant_id=tenant_id,
                    slug=f"eval-{_slugify(name, 'workspace')}-{workspace_id[:8]}",
                    name=name,
                    status="active",
                    # Stamped from the sandbox's own clock rather than left to
                    # the column default. The expiry the evaluator is shown and
                    # the expiry the sweep enforces have to be the same reading,
                    # and a column default is a different one.
                    created_at=now,
                    updated_at=now,
                )
            )
        return WorkspaceView(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            company_name=name,
            primary_domain=(primary_domain or "").strip() or None,
            created_at=now,
            expires_at=now + timedelta(hours=self.limits.ttl_hours),
            credential_storage=self.store.describe(),
        )

    def get_workspace(self, workspace_id: str) -> WorkspaceView:
        tenant_id = self.tenant_for(workspace_id)
        with self.database.read_session() as session:
            row = TenantRepository(session).get(tenant_id)
            if row is None:
                raise SandboxNotFoundError("no such workspace")
            created = as_utc(row.created_at)
            return WorkspaceView(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                company_name=row.name,
                primary_domain=None,
                created_at=created,
                expires_at=created + timedelta(hours=self.limits.ttl_hours),
                credential_storage=self.store.describe(),
            )

    def tenant_for(self, workspace_id: str) -> str:
        """Derive the tenant from the workspace id, and refuse anything else.

        The identifier is the authority here: it is thirty-two random hex
        characters, it is never listed, and it is the only thing that reaches a
        workspace. Deriving the tenant rather than accepting one is what stops a
        caller aiming a sandbox route at the demonstration tenant.
        """

        value = (workspace_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise SandboxNotFoundError("that is not a workspace identifier")
        return f"{WORKSPACE_PREFIX}{value}"

    def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        tenant_id = self.tenant_for(workspace_id)
        secrets_destroyed = 0
        with self.database.read_session() as session:
            existing = TenantRepository(session).get(tenant_id)
            if existing is None:
                raise SandboxNotFoundError("no such workspace")
        for instance in self.connectors.list_instances(tenant_id):
            if not instance.credential_ref:
                continue
            self.store.delete(self._secret_name(workspace_id, instance.connector_key))
            secrets_destroyed += 1
        for grant in self.connectors.list_grants(tenant_id):
            if grant.status == "active":
                self.connectors.revoke_grant(
                    tenant_id,
                    grant.grant_id,
                    actor_id="evaluator-sandbox",
                    reason="workspace deleted",
                )
        with self.database.transaction() as session:
            row = TenantRepository(session).get(tenant_id)
            if row is not None:
                session.delete(row)
        return {"workspace_id": workspace_id, "deleted": True, "secrets_destroyed": secrets_destroyed}

    def _sweep_expired(self) -> int:
        """Delete workspaces past their life, and the credentials with them.

        Called on creation rather than on a timer, because a service that scales
        to zero has no timer, and the moment somebody asks for a workspace is
        the moment the ceiling matters.
        """

        cutoff = self.clock() - timedelta(hours=self.limits.ttl_hours)
        with self.database.read_session() as session:
            rows = session.scalars(
                select(Tenant).where(Tenant.tenant_id.like(f"{WORKSPACE_PREFIX}%"))
            ).all()
            stale = [row.tenant_id for row in rows if as_utc(row.created_at) < cutoff]
        for tenant_id in stale:
            try:
                self.delete_workspace(tenant_id.removeprefix(WORKSPACE_PREFIX))
            except SandboxError:
                continue
        return len(stale)

    # -- connectors --------------------------------------------------------

    @staticmethod
    def _secret_name(workspace_id: str, connector_key: str) -> str:
        return f"{SECRET_NAME_PREFIX}{workspace_id}-{connector_key}"

    def approve_grant(
        self,
        workspace_id: str,
        connector_instance_id: str,
        *,
        streams: list[str],
        scope_value: str,
        purpose: str,
        hours: int | None = None,
    ) -> dict[str, Any]:
        """Approve a further read on a connector that is already attached.

        An audit needs two streams where the first attachment approved one, and
        the alternative -- widening the original grant -- would rewrite an
        authority after the fact. A grant is a record of what somebody approved
        and when; a second approval is a second record.

        The credential is not touched and is not re-supplied. It stays where the
        attachment put it, which is the reason a grant and a credential are
        separate things in the first place.
        """

        workspace = self.get_workspace(workspace_id)
        instance = self._instance(workspace.tenant_id, connector_instance_id)
        profile = self._profile(instance.connector_type)
        if not streams:
            raise SandboxError("a grant must name at least one stream")
        selector_keys = set()
        for name in streams:
            selector_keys.add(profile.stream(name).selector_key)
        if len(selector_keys) != 1:
            raise SandboxError(
                "these streams are scoped by different keys and cannot share one grant"
            )
        selectors = profile.stream(streams[0]).selectors_for(scope_value)
        grant = self.connectors.create_grant(
            workspace.tenant_id,
            connector_instance_id,
            CollectionGrantInput(
                grant_key=f"{instance.connector_key}-{'-'.join(sorted(streams))}-{secrets.token_hex(3)}",
                purpose=purpose,
                allowed_streams=list(streams),
                resource_selectors=selectors,
                approved_by="evaluator",
                expires_at=self.clock() + timedelta(hours=hours or self.limits.grant_hours),
            ),
        )
        return {
            "grant_id": grant.grant_id,
            "purpose": grant.purpose,
            "allowed_streams": grant.allowed_streams,
            "resource_selectors": grant.resource_selectors,
            "read_only": grant.read_only,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        }

    def revoke_grant(self, workspace_id: str, grant_id: str, *, reason: str) -> dict[str, Any]:
        """Close a grant when the work it authorised is finished.

        A grant that outlives its collection is the finding this product raises
        against other people, so the audit ends by closing its own.
        """

        workspace = self.get_workspace(workspace_id)
        grant = self.connectors.revoke_grant(
            workspace.tenant_id, grant_id, actor_id="evaluator", reason=reason
        )
        return {
            "grant_id": grant.grant_id,
            "status": grant.status,
            "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
            "reason": grant.revocation_reason,
        }

    def connector(self, workspace_id: str, connector_instance_id: str) -> ConnectorInstanceView:
        return self._instance(self.get_workspace(workspace_id).tenant_id, connector_instance_id)

    def connect(
        self,
        workspace_id: str,
        *,
        provider: str,
        base_url: str,
        stream: str,
        scope_value: str,
        credentials: dict[str, str] | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Attach one provider account, approve one grant, and store nothing else.

        The grant is created in the same call as the instance on purpose. A
        connector with no grant collects nothing, so registering one without
        approving what it may read would leave a half-configured connector on
        screen and put the interesting half of the model out of sight.
        """

        workspace = self.get_workspace(workspace_id)
        profile = self._profile(provider)
        stream_profile = profile.stream(stream)
        scope = stream_profile.scope_for(scope_value)
        selectors = stream_profile.selectors_for(scope_value)
        validate_provider_url(base_url, profile)

        values = {key: str(value).strip() for key, value in (credentials or {}).items()}
        supplied = {key: value for key, value in values.items() if value}
        connector_key = f"{profile.connector_type}-{secrets.token_hex(4)}"
        credential_ref: str | None = None
        if supplied:
            headers = profile.headers(supplied)
            credential_ref = self.store.put(self._secret_name(workspace_id, connector_key), headers)
        elif profile.connector_type != "github":
            # Only GitHub has a meaningful unauthenticated read. Everywhere else
            # an empty credential produces a 401 several steps later, and a
            # refusal now names the actual problem.
            raise SandboxError(f"{profile.display_name} requires a credential")

        instance = self.connectors.register_instance(
            workspace.tenant_id,
            ConnectorInstanceInput(
                connector_key=connector_key,
                connector_type=profile.connector_type,
                display_name=display_name or profile.display_name,
                base_url=base_url.rstrip("/"),
                credential_ref=credential_ref,
                config={
                    "evaluator_sandbox": True,
                    "allowed_hosts": list(profile.allowed_hosts),
                    "authenticated": bool(credential_ref),
                },
            ),
        )
        grant = self.connectors.create_grant(
            workspace.tenant_id,
            instance.connector_instance_id,
            CollectionGrantInput(
                grant_key=f"{connector_key}-{stream}",
                purpose=f"Evaluator collection of {stream_profile.label.lower()} for {workspace.company_name}",
                allowed_streams=[stream],
                resource_selectors=selectors,
                approved_by="evaluator",
                expires_at=self.clock() + timedelta(hours=self.limits.grant_hours),
            ),
        )
        return {
            "connector": self._instance_card(instance),
            "grant": {
                "grant_id": grant.grant_id,
                "purpose": grant.purpose,
                "allowed_streams": grant.allowed_streams,
                "resource_selectors": grant.resource_selectors,
                "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
                "read_only": grant.read_only,
            },
            "stream": stream,
            "scope": scope,
        }

    def health(self, workspace_id: str, connector_instance_id: str) -> dict[str, Any]:
        """Ask the provider whether the credential works, before collecting anything.

        This is the check worth running first, because it separates "your token
        is wrong" from "your scope is wrong", and those two failures otherwise
        arrive looking identical several seconds apart.
        """

        workspace = self.get_workspace(workspace_id)
        instance = self._instance(workspace.tenant_id, connector_instance_id)
        connector = self._build(instance)
        result = connector.health()
        return {
            "status": result.status,
            "checked_at": result.checked_at.isoformat(),
            "details": result.details,
        }

    def collect(
        self,
        workspace_id: str,
        connector_instance_id: str,
        *,
        stream: str,
        scope_value: str,
        engagement_id: str | None = None,
        task_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        max_pages: int | None = None,
        max_objects: int | None = None,
        max_requests: int | None = None,
    ) -> ConnectorRunSummary:
        workspace = self.get_workspace(workspace_id)
        instance = self._instance(workspace.tenant_id, connector_instance_id)
        profile = self._profile(instance.connector_type)
        stream_profile = profile.stream(stream)
        scope = stream_profile.scope_for(scope_value)
        grant = self._active_grant(workspace.tenant_id, connector_instance_id, stream)
        connector = _BoundedConnector(
            self._build(instance, max_requests=max_requests),
            max_pages=max_pages or self.limits.max_pages,
            max_objects=max_objects or self.limits.max_objects,
        )
        return self.connectors.run(
            tenant_id=workspace.tenant_id,
            connector_instance_id=connector_instance_id,
            grant_id=grant,
            connector=connector,
            request=CollectionRequest(
                stream=stream,
                scope=scope,
                parameters=dict(parameters or {}),
                engagement_id=engagement_id,
                task_id=task_id,
                classification="confidential",
            ),
            idempotency_key=f"sandbox:{connector_instance_id}:{stream}:{secrets.token_hex(6)}",
        )

    def list_connectors(self, workspace_id: str) -> list[dict[str, Any]]:
        tenant_id = self.get_workspace(workspace_id).tenant_id
        return [self._instance_card(item) for item in self.connectors.list_instances(tenant_id)]

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _profile(provider: str) -> ProviderProfile:
        profile = PROVIDERS.get(provider)
        if profile is None:
            known = ", ".join(sorted(PROVIDERS))
            raise SandboxError(f"unknown provider {provider!r}; this build offers {known}")
        return profile

    def _instance(self, tenant_id: str, connector_instance_id: str) -> ConnectorInstanceView:
        for item in self.connectors.list_instances(tenant_id):
            if item.connector_instance_id == connector_instance_id:
                return item
        raise SandboxNotFoundError("no such connector in this workspace")

    def _active_grant(self, tenant_id: str, connector_instance_id: str, stream: str) -> str:
        now = self.clock()
        for grant in self.connectors.list_grants(tenant_id, connector_instance_id):
            if grant.status != "active" or stream not in grant.allowed_streams:
                continue
            if grant.expires_at is not None and as_utc(grant.expires_at) <= now:
                continue
            return grant.grant_id
        raise SandboxError(
            f"no live grant permits {stream!r} on this connector; attach it again to approve one"
        )

    def _build(self, instance: ConnectorInstanceView, *, max_requests: int | None = None) -> Any:
        profile = self._profile(instance.connector_type)
        # One transport per build, so its request counter bounds this operation
        # rather than the lifetime of the process.
        def transport() -> HttpTransport:
            built = self._transport_factory(profile)
            if max_requests is not None and isinstance(built, GuardedTransport):
                built.max_requests = max_requests
            return built

        factory = ConnectorFactory(credentials=self.resolver, transport_factory=transport)
        if not instance.credential_ref:
            # The factory requires a credential reference, which is right for a
            # tenant's own connector and wrong for the one provider that reads
            # public data without one. Build it directly rather than loosening
            # the factory's rule for everybody.
            from .connectors.adapters import GitHubPullRequestConnector

            return GitHubPullRequestConnector(
                instance.base_url or "",
                transport(),
                credential=StaticHeaderCredential({}),
            )
        return factory.build(instance)

    @staticmethod
    def _instance_card(instance: ConnectorInstanceView) -> dict[str, Any]:
        return {
            "connector_instance_id": instance.connector_instance_id,
            "connector_type": instance.connector_type,
            "display_name": instance.display_name,
            "base_url": instance.base_url,
            "status": instance.status,
            "authenticated": bool(instance.credential_ref),
            "last_health_status": instance.last_health_status,
            "last_health_checked_at": (
                instance.last_health_checked_at.isoformat()
                if instance.last_health_checked_at
                else None
            ),
        }


@dataclass
class _BoundedConnector:
    """Stops a collection at a ceiling, and stops it between pages.

    Wrapping rather than parameterising :class:`ConnectorService` keeps the
    product's own runs exactly as they are: an audit owner who asked for a
    population gets the population, and a stranger gets the first few pages of
    it.
    """

    inner: Any
    max_pages: int
    max_objects: int

    @property
    def descriptor(self) -> Any:
        return self.inner.descriptor

    def health(self) -> Any:
        return self.inner.health()

    def scope_for(self, request: CollectionRequest) -> dict[str, object]:
        return self.inner.scope_for(request)

    def collect_pages(self, request: CollectionRequest, checkpoint: dict[str, object]) -> Iterator[Any]:
        seen = 0
        for index, page in enumerate(self.inner.collect_pages(request, checkpoint), start=1):
            remaining = self.max_objects - seen
            if remaining <= 0:
                return
            if len(page.objects) > remaining:
                page = page.model_copy(update={"objects": page.objects[:remaining]})
            seen += len(page.objects)
            yield page
            if index >= self.max_pages or seen >= self.max_objects:
                return
