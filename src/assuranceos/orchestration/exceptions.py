from __future__ import annotations

from .definitions import FailureClass


class OrchestrationError(RuntimeError):
    """Base class for deterministic orchestration failures."""


class WorkflowValidationError(OrchestrationError, ValueError):
    pass


class WorkflowAlreadyCompiledError(OrchestrationError):
    pass


class EngagementNotFoundError(OrchestrationError):
    pass


class TaskNotFoundError(OrchestrationError):
    pass


class InvalidStateTransitionError(OrchestrationError):
    pass


class LeaseConflictError(OrchestrationError):
    pass


class TaskExecutionError(RuntimeError):
    failure_class: FailureClass

    def __init__(self, message: str, *, failure_class: FailureClass):
        super().__init__(message)
        self.failure_class = failure_class


class RetryableTaskError(TaskExecutionError):
    def __init__(
        self,
        message: str,
        *,
        failure_class: FailureClass = FailureClass.TRANSIENT_INFRASTRUCTURE,
    ):
        super().__init__(message, failure_class=failure_class)


class PermanentTaskError(TaskExecutionError):
    def __init__(
        self,
        message: str,
        *,
        failure_class: FailureClass = FailureClass.CONFIGURATION_ERROR,
    ):
        super().__init__(message, failure_class=failure_class)
