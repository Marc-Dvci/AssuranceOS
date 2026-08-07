from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from assuranceos.db.models import EngagementTask, TaskDependency
from assuranceos.db.repositories import EngagementRepository, new_id

from .definitions import TaskDefinition, TaskStatus, WorkflowDefinition
from .exceptions import WorkflowAlreadyCompiledError, WorkflowValidationError


class WorkflowCompiler:
    """Validates and persists a versioned task graph.

    The compiler intentionally knows nothing about workers or queues. Its output is canonical task
    and dependency state that can be executed by any runtime implementing the orchestration
    contract.
    """

    def validate(self, workflow: WorkflowDefinition) -> None:
        keys = [task.key for task in workflow.tasks]
        if len(keys) != len(set(keys)):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise WorkflowValidationError(f"duplicate task keys: {', '.join(duplicates)}")

        known = set(keys)
        for task in workflow.tasks:
            dependencies = [dependency.task_key for dependency in task.dependencies]
            if len(dependencies) != len(set(dependencies)):
                raise WorkflowValidationError(f"task {task.key!r} declares a dependency twice")
            missing = sorted(set(dependencies) - known)
            if missing:
                raise WorkflowValidationError(
                    f"task {task.key!r} references unknown dependencies: {', '.join(missing)}"
                )
            if task.key in dependencies:
                raise WorkflowValidationError(f"task {task.key!r} cannot depend on itself")

        self._assert_acyclic(workflow.tasks)

    @staticmethod
    def _assert_acyclic(tasks: list[TaskDefinition]) -> None:
        indegree = {task.key: 0 for task in tasks}
        children: dict[str, list[str]] = defaultdict(list)
        for task in tasks:
            for dependency in task.dependencies:
                indegree[task.key] += 1
                children[dependency.task_key].append(task.key)

        ready = deque(sorted(key for key, degree in indegree.items() if degree == 0))
        visited = 0
        while ready:
            key = ready.popleft()
            visited += 1
            for child in sorted(children[key]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

        if visited != len(tasks):
            cyclic = sorted(key for key, degree in indegree.items() if degree > 0)
            raise WorkflowValidationError(
                f"workflow contains a dependency cycle involving: {', '.join(cyclic)}"
            )

    def compile(
        self,
        session: Session,
        *,
        tenant_id: str,
        engagement_id: str,
        workflow: WorkflowDefinition,
        compiled_at: datetime,
    ) -> list[EngagementTask]:
        self.validate(workflow)
        repository = EngagementRepository(session)
        if repository.list_tasks(tenant_id, engagement_id):
            raise WorkflowAlreadyCompiledError(
                f"engagement {engagement_id!r} already has a compiled task graph"
            )

        task_ids = {task.key: new_id("tsk") for task in workflow.tasks}
        rows: list[EngagementTask] = []
        for definition in workflow.tasks:
            deadline_at = (
                compiled_at + timedelta(seconds=definition.deadline_seconds)
                if definition.deadline_seconds is not None
                else None
            )
            row = EngagementTask(
                task_id=task_ids[definition.key],
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                task_key=definition.key,
                task_type=definition.task_type,
                definition_version=definition.definition_version,
                status=TaskStatus.PENDING,
                priority=definition.priority,
                assigned_agent_role=definition.assigned_agent_role,
                input_refs_json=list(definition.input_refs),
                idempotency_key=(
                    f"{engagement_id}:{workflow.workflow_version}:{definition.key}:"
                    f"{definition.definition_version}"
                ),
                execution_policy_json=definition.persisted_execution_policy(),
                model_policy=definition.model_policy,
                tool_policy=definition.tool_policy,
                human_gate=definition.human_gate,
                deadline_at=deadline_at,
            )
            repository.add_task(row)
            rows.append(row)

        for definition in workflow.tasks:
            for dependency in definition.dependencies:
                repository.add_dependency(
                    TaskDependency(
                        dependency_id=new_id("dep"),
                        tenant_id=tenant_id,
                        task_id=task_ids[definition.key],
                        depends_on_task_id=task_ids[dependency.task_key],
                        condition_json={
                            "allowed_statuses": sorted(
                                status.value for status in dependency.allowed_statuses
                            )
                        },
                    )
                )
        return rows
