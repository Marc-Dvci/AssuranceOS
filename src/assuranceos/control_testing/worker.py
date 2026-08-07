from __future__ import annotations

from collections.abc import Callable

from assuranceos.orchestration.definitions import TaskExecutionResult, TaskLease

from .definitions import ControlTestRunRequest
from .service import ControlTestService


class ControlTestTaskHandler:
    """Adapts deterministic-test execution to the durable orchestration worker contract."""

    def __init__(
        self,
        service: ControlTestService,
        request_loader: Callable[[TaskLease], ControlTestRunRequest],
    ):
        self.service = service
        self.request_loader = request_loader

    def __call__(self, lease: TaskLease) -> TaskExecutionResult:
        request = self.request_loader(lease)
        if request.engagement_id is None:
            request = request.model_copy(
                update={"engagement_id": lease.engagement_id, "task_id": lease.task_id}
            )
        result = self.service.run(lease.tenant_id, request)
        return TaskExecutionResult(
            output_refs=[f"control-test-run:{result.run_id}"],
            result=result.model_dump(mode="json"),
        )
