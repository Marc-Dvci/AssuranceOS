from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .definitions import (
    EngagementSnapshot,
    FailureClass,
    TaskExecutionResult,
    TaskLease,
    TaskStatus,
)
from .exceptions import PermanentTaskError, RetryableTaskError, TaskExecutionError
from .service import Orchestrator

TaskHandler = Callable[[TaskLease], TaskExecutionResult]


@dataclass(frozen=True)
class WorkerRun:
    task_id: str
    task_key: str
    outcome: str
    attempt_count: int


class LocalWorker:
    """Small synchronous worker adapter for tests, local execution, and Cloud Run Jobs.

    Queue delivery is deliberately outside this class. A Pub/Sub subscriber, CLI loop, or test can
    call ``run_once`` with identical orchestration semantics.
    """

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        tenant_id: str,
        worker_id: str,
        handlers: Mapping[str, TaskHandler],
        lease_seconds: int = 60,
    ):
        self.orchestrator = orchestrator
        self.tenant_id = tenant_id
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.lease_seconds = lease_seconds

    def run_once(self, *, engagement_id: str | None = None) -> WorkerRun | None:
        self.orchestrator.tick(tenant_id=self.tenant_id)
        lease = self.orchestrator.claim_next(
            tenant_id=self.tenant_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            engagement_id=engagement_id,
            task_types=self.handlers.keys(),
        )
        if lease is None:
            return None

        handler = self.handlers.get(lease.task_type)
        if handler is None:
            # This branch is defensive: claim filtering normally prevents it.
            self.orchestrator.fail_task(
                tenant_id=self.tenant_id,
                task_id=lease.task_id,
                worker_id=self.worker_id,
                failure_class=FailureClass.CONFIGURATION_ERROR,
                message=f"no handler registered for task type {lease.task_type!r}",
                force_retryable=False,
            )
            return WorkerRun(
                task_id=lease.task_id,
                task_key=lease.task_key,
                outcome="failed",
                attempt_count=lease.attempt_count,
            )

        try:
            result = handler(lease)
        except RetryableTaskError as exc:
            outcome = self._fail(lease, exc, force_retryable=True)
        except PermanentTaskError as exc:
            outcome = self._fail(lease, exc, force_retryable=False)
        except TaskExecutionError as exc:
            outcome = self._fail(lease, exc, force_retryable=None)
        except Exception as exc:  # noqa: BLE001 - worker boundary must classify handler failures
            snapshot = self.orchestrator.fail_task(
                tenant_id=self.tenant_id,
                task_id=lease.task_id,
                worker_id=self.worker_id,
                failure_class=FailureClass.INTERNAL_ERROR,
                message=f"unhandled handler error: {type(exc).__name__}: {exc}",
            )
            outcome = self._outcome_for(snapshot, lease.task_id)
        else:
            self.orchestrator.complete_task(
                tenant_id=self.tenant_id,
                task_id=lease.task_id,
                worker_id=self.worker_id,
                result=result,
            )
            outcome = "completed"

        return WorkerRun(
            task_id=lease.task_id,
            task_key=lease.task_key,
            outcome=outcome,
            attempt_count=lease.attempt_count,
        )

    def _fail(
        self,
        lease: TaskLease,
        exc: TaskExecutionError,
        *,
        force_retryable: bool | None,
    ) -> str:
        snapshot = self.orchestrator.fail_task(
            tenant_id=self.tenant_id,
            task_id=lease.task_id,
            worker_id=self.worker_id,
            failure_class=exc.failure_class,
            message=str(exc),
            force_retryable=force_retryable,
        )
        return self._outcome_for(snapshot, lease.task_id)

    @staticmethod
    def _outcome_for(snapshot: EngagementSnapshot, task_id: str) -> str:
        status = next(task.status for task in snapshot.tasks if task.task_id == task_id)
        return "retry_scheduled" if status == TaskStatus.RETRY_WAIT else "failed"
