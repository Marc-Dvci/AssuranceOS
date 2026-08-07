from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from .definitions import (
    CollectionRequest,
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorPage,
)


class Connector(Protocol):
    """Minimal read-only collection contract implemented by every connector."""

    descriptor: ConnectorDescriptor

    def health(self) -> ConnectorHealth: ...

    def scope_for(self, request: CollectionRequest) -> dict[str, object]: ...

    def collect_pages(
        self,
        request: CollectionRequest,
        checkpoint: dict[str, object],
    ) -> Iterator[ConnectorPage]: ...
