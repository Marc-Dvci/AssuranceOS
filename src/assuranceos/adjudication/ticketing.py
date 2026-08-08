"""Write adapters that open a remediation ticket exactly once.

Everything else in this repository is a read connector. Remediation is the first
place AssuranceOS writes into a system of record, and a write is where an
idempotency mistake stops being an internal inconsistency and becomes twenty
duplicate tickets in somebody's queue.

Duplication is prevented twice, deliberately:

1. **Locally.** The remediation action carries ``external_ref``. If it is set, no
   provider call is made at all.
2. **At the provider.** Every create is preceded by a lookup on a correlation key
   derived from the action id. This is the half that survives the interesting
   failure: a crash *after* the provider created the ticket but *before* the local
   commit leaves state that says "no ticket" and a provider that says otherwise.
   Only the remote lookup can resolve that disagreement, so it is not optional.

Correlation uses a native field on each provider — ServiceNow's ``correlation_id``,
a reserved Jira label — rather than fuzzy matching on summary text, because a
ticket someone renamed must still be recognised as the same ticket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from ..connectors.adapters.common import RestAdapter
from ..connectors.credentials import CredentialResolver
from ..connectors.definitions import ConnectorInstanceView
from ..connectors.exceptions import ConnectorProtocolError
from ..connectors.transport import HttpTransport, HttpxTransport
from .exceptions import TicketingError

#: Prefix that makes an AssuranceOS correlation key recognisable in a provider
#: whose records come from many sources.
CORRELATION_PREFIX = "assuranceos"


def correlation_key(action_id: str) -> str:
    """The stable key a remediation action is filed under in any provider.

    Derived from the action id rather than from a caller-supplied idempotency key
    so that two callers who disagree about the key still address the same ticket.
    """
    return f"{CORRELATION_PREFIX}:{action_id}"


@dataclass(frozen=True)
class TicketRequest:
    """What a provider needs in order to open a remediation ticket."""

    action_id: str
    finding_code: str
    title: str
    description: str
    owner_ref: str
    due_date: date
    severity: str
    project_or_table: str
    labels: tuple[str, ...] = ()

    @property
    def correlation_key(self) -> str:
        return correlation_key(self.action_id)


@dataclass(frozen=True)
class TicketRef:
    """A ticket in an external system.

    ``created`` distinguishes the ticket this call opened from the one it found
    already open. The caller records the difference rather than discarding it: a
    sync that keeps reporting ``created=True`` for the same action is a duplicate
    bug announcing itself.
    """

    system: str
    external_ref: str
    url: str | None = None
    created: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class TicketWriter(Protocol):
    """The write contract shared by every remediation provider."""

    system: str

    def create_or_get(self, request: TicketRequest) -> TicketRef: ...


class NullTicketWriter:
    """The writer used when a remediation is tracked inside AssuranceOS only.

    Present so that ``external_system="none"`` follows the same code path as a
    real provider instead of being a branch the tests never take.
    """

    system = "none"

    def create_or_get(self, request: TicketRequest) -> TicketRef:
        return TicketRef(
            system=self.system,
            external_ref=request.correlation_key,
            url=None,
            created=False,
            details={"tracked_in": "assuranceos"},
        )


class JiraTicketWriter(RestAdapter):
    """Opens a Jira issue for a remediation action, at most once.

    Jira has no first-class correlation field on an issue, so the key is written
    as a reserved label and looked up with a JQL ``labels = ...`` term. Labels are
    indexed and exact-match, which is what a correlation lookup needs; searching
    the summary text is not, because summaries get edited.
    """

    system = "jira"

    def __init__(self, *args: Any, issue_type: str = "Task", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.issue_type = issue_type

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    def _find(self, request: TicketRequest) -> TicketRef | None:
        response = self.request(
            "POST",
            "/rest/api/3/search/jql",
            headers=self._headers(),
            json_body={
                "jql": (
                    f"project = {request.project_or_table} "
                    f'AND labels = "{request.correlation_key}"'
                ),
                "maxResults": 2,
                "fields": ["summary", "status"],
            },
        )
        payload = response.json_body
        issues = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(issues, list):
            raise ConnectorProtocolError("Jira search response must contain an issues array")
        if not issues:
            return None
        if len(issues) > 1:
            # Two issues carrying one correlation key means the invariant this
            # class exists to hold has already been broken upstream. Refusing is
            # the only honest response: picking one would hide it.
            raise ConnectorProtocolError(
                f"correlation key {request.correlation_key!r} matches "
                f"{len(issues)} Jira issues; remediation must map to exactly one"
            )
        key = str(issues[0].get("key"))
        return TicketRef(
            system=self.system,
            external_ref=key,
            url=self.url(f"/browse/{key}"),
            created=False,
            details={"matched_by": "correlation_label"},
        )

    def create_or_get(self, request: TicketRequest) -> TicketRef:
        existing = self._find(request)
        if existing is not None:
            return existing
        response = self.request(
            "POST",
            "/rest/api/3/issue",
            headers=self._headers(),
            json_body={
                "fields": {
                    "project": {"key": request.project_or_table},
                    "summary": request.title[:255],
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": request.description}],
                            }
                        ],
                    },
                    "issuetype": {"name": self.issue_type},
                    "duedate": request.due_date.isoformat(),
                    "labels": [request.correlation_key, *request.labels],
                }
            },
        )
        payload = response.json_body
        key = payload.get("key") if isinstance(payload, dict) else None
        if not key:
            raise ConnectorProtocolError("Jira issue creation returned no issue key")
        return TicketRef(
            system=self.system,
            external_ref=str(key),
            url=self.url(f"/browse/{key}"),
            created=True,
            details={"issue_type": self.issue_type},
        )


class ServiceNowTicketWriter(RestAdapter):
    """Opens a ServiceNow record for a remediation action, at most once.

    ServiceNow carries ``correlation_id`` on task-derived tables precisely for
    integrations like this one, so the correlation lookup is a native indexed
    query rather than a convention layered on top.
    """

    system = "servicenow"

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Content-Type": "application/json"}

    @staticmethod
    def _records(payload: Any, *, context: str) -> list[dict[str, Any]]:
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        raise ConnectorProtocolError(f"ServiceNow {context} response must contain a result")

    def _find(self, request: TicketRequest) -> TicketRef | None:
        response = self.request(
            "GET",
            f"/api/now/table/{request.project_or_table}",
            headers=self._headers(),
            params={
                "sysparm_query": f"correlation_id={request.correlation_key}",
                "sysparm_fields": "sys_id,number,short_description",
                "sysparm_limit": 2,
            },
        )
        records = self._records(response.json_body, context="query")
        if not records:
            return None
        if len(records) > 1:
            raise ConnectorProtocolError(
                f"correlation key {request.correlation_key!r} matches "
                f"{len(records)} ServiceNow records; remediation must map to exactly one"
            )
        record = records[0]
        number = str(record.get("number") or record.get("sys_id"))
        return TicketRef(
            system=self.system,
            external_ref=number,
            url=self.url(f"/{request.project_or_table}.do?sys_id={record.get('sys_id')}"),
            created=False,
            details={"matched_by": "correlation_id"},
        )

    def create_or_get(self, request: TicketRequest) -> TicketRef:
        existing = self._find(request)
        if existing is not None:
            return existing
        response = self.request(
            "POST",
            f"/api/now/table/{request.project_or_table}",
            headers=self._headers(),
            json_body={
                "short_description": request.title[:160],
                "description": request.description,
                "assigned_to": request.owner_ref,
                "due_date": request.due_date.isoformat(),
                "correlation_id": request.correlation_key,
                "correlation_display": f"AssuranceOS {request.finding_code}",
                "u_severity": request.severity,
            },
        )
        records = self._records(response.json_body, context="insert")
        if not records:
            raise ConnectorProtocolError("ServiceNow insert returned no record")
        record = records[0]
        number = record.get("number") or record.get("sys_id")
        if not number:
            raise ConnectorProtocolError("ServiceNow insert returned no record number")
        return TicketRef(
            system=self.system,
            external_ref=str(number),
            url=self.url(f"/{request.project_or_table}.do?sys_id={record.get('sys_id')}"),
            created=True,
            details={"table": request.project_or_table},
        )


#: Writers reachable by ``external_system`` on a remediation request.
TICKET_WRITERS: dict[str, type[Any]] = {
    "jira": JiraTicketWriter,
    "servicenow": ServiceNowTicketWriter,
}


def writer_from_connector(
    instance: ConnectorInstanceView,
    *,
    credentials: CredentialResolver | None = None,
    transport: HttpTransport | None = None,
) -> TicketWriter:
    """Build a write adapter from one active, tenant-owned connector record.

    Credential material is resolved only at the network boundary.  It is never
    copied into canonical connector metadata, logs, ticket details, or responses.
    """
    if instance.status != "active":
        raise TicketingError(
            f"connector {instance.connector_key!r} is not active"
        )
    writer_type = TICKET_WRITERS.get(instance.connector_type)
    if writer_type is None:
        raise TicketingError(
            f"connector type {instance.connector_type!r} cannot file remediation tickets"
        )
    if not instance.base_url:
        raise TicketingError("ticket connector requires a base_url")
    if not instance.credential_ref:
        raise TicketingError("ticket connector requires a credential reference")
    try:
        credential = (credentials or CredentialResolver()).resolve(instance.credential_ref)
    except (RuntimeError, ValueError) as exc:
        raise TicketingError(f"ticket connector credential is unavailable: {exc}") from exc

    kwargs: dict[str, Any] = {
        "base_url": instance.base_url,
        "transport": transport or HttpxTransport(),
        "credential": credential,
    }
    if instance.connector_type == "jira":
        kwargs["issue_type"] = str(instance.config.get("issue_type", "Task"))
    return writer_type(**kwargs)
