class ConnectorError(RuntimeError):
    """Base class for connector failures."""


class ConnectorNotFoundError(ConnectorError):
    pass


class CollectionGrantError(ConnectorError):
    pass


class CollectionGrantExpiredError(CollectionGrantError):
    pass


class CollectionScopeError(CollectionGrantError):
    pass


class ConnectorRunConflictError(ConnectorError):
    pass


class ConnectorProtocolError(ConnectorError):
    pass


class ConnectorAuthenticationError(ConnectorError):
    pass


class ConnectorPermissionError(ConnectorError):
    pass


class ConnectorRateLimitError(ConnectorError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ConnectorUnavailableError(ConnectorError):
    pass


class SourceVersionConflictError(ConnectorProtocolError):
    pass
