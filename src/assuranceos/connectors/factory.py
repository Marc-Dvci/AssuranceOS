from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .adapters import (
    ConfluencePageConnector,
    GitHubPullRequestConnector,
    GoogleDriveFileConnector,
    JiraIssueConnector,
)
from .credentials import CredentialResolver
from .definitions import ConnectorInstanceView
from .protocol import Connector
from .transport import HttpTransport, HttpxTransport


@dataclass(frozen=True)
class ConnectorFactory:
    """Instantiate an approved live adapter from canonical connector metadata.

    The factory has no secret storage responsibility. It resolves only the registered credential
    reference and injects a bounded HTTP transport. Unsupported connector types fail closed.
    """

    credentials: CredentialResolver
    transport_factory: Callable[[], HttpTransport] = HttpxTransport

    def build(self, instance: ConnectorInstanceView) -> Connector:
        if not instance.base_url:
            raise ValueError("live REST connector instances require base_url")
        if not instance.credential_ref:
            raise ValueError("live REST connector instances require credential_ref")
        credential = self.credentials.resolve(instance.credential_ref)
        kwargs = {
            "base_url": instance.base_url,
            "transport": self.transport_factory(),
            "credential": credential,
        }
        builders = {
            "github": GitHubPullRequestConnector,
            "jira": JiraIssueConnector,
            "confluence": ConfluencePageConnector,
            "google_drive": GoogleDriveFileConnector,
        }
        builder = builders.get(instance.connector_type)
        if builder is None:
            raise ValueError(f"unsupported live connector type: {instance.connector_type}")
        if instance.connector_type == "github" and instance.config.get("api_version"):
            kwargs["api_version"] = str(instance.config["api_version"])
        return builder(**kwargs)
