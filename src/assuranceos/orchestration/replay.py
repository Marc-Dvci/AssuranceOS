from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from assuranceos.db import Database
from assuranceos.db.repositories import AuditEventRepository

from .definitions import EngagementSnapshot, EngagementStatus, TaskStatus
from .service import Orchestrator


class ReplayTask(BaseModel):
    task_id: str
    task_key: str
    status: TaskStatus
    attempt_count: int = 0


class ReplayProjection(BaseModel):
    tenant_id: str
    engagement_id: str
    engagement_status: EngagementStatus = EngagementStatus.PLANNED
    tasks: dict[str, ReplayTask] = Field(default_factory=dict)


class ReplayComparison(BaseModel):
    matches: bool
    replayed_engagement_status: EngagementStatus
    canonical_engagement_status: EngagementStatus
    task_status_mismatches: dict[str, dict[str, str]] = Field(default_factory=dict)
    missing_in_replay: list[str] = Field(default_factory=list)
    missing_in_canonical: list[str] = Field(default_factory=list)


def replay_events(
    events: list[dict], *, tenant_id: str, engagement_id: str
) -> ReplayProjection:
    projection = ReplayProjection(tenant_id=tenant_id, engagement_id=engagement_id)
    streams: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        streams[event["stream_id"]].append(event)

    # A task stream is independent from other task streams. The engagement stream carries the
    # authoritative aggregate status. This avoids relying on wall-clock ordering across workers.
    for stream_events in streams.values():
        stream_events.sort(key=lambda item: (item["sequence_no"], item["event_id"]))
        for event in stream_events:
            payload = event["payload"]
            if event["event_type"] == "orchestration.task.created":
                assert event["task_id"] is not None
                projection.tasks[event["task_id"]] = ReplayTask(
                    task_id=event["task_id"],
                    task_key=payload["task_key"],
                    status=TaskStatus(payload["status"]),
                )
            elif event["event_type"] == "orchestration.task.transitioned":
                task_id = event["task_id"]
                if task_id is None or task_id not in projection.tasks:
                    continue
                task = projection.tasks[task_id]
                task.status = TaskStatus(payload["to_status"])
                task.attempt_count = int(payload.get("attempt_count", task.attempt_count))
            elif event["event_type"] == "orchestration.engagement.transitioned":
                projection.engagement_status = EngagementStatus(payload["to_status"])
    return projection


def verify_replay(
    database: Database,
    orchestrator: Orchestrator,
    *,
    tenant_id: str,
    engagement_id: str,
) -> ReplayComparison:
    with database.read_session() as session:
        events = AuditEventRepository(session).list(tenant_id, engagement_id)
    replayed = replay_events(events, tenant_id=tenant_id, engagement_id=engagement_id)
    canonical = orchestrator.snapshot(tenant_id=tenant_id, engagement_id=engagement_id)
    return compare_projection(replayed, canonical)


def compare_projection(
    replayed: ReplayProjection, canonical: EngagementSnapshot
) -> ReplayComparison:
    canonical_by_id = {task.task_id: task for task in canonical.tasks}
    replay_ids = set(replayed.tasks)
    canonical_ids = set(canonical_by_id)
    mismatches: dict[str, dict[str, str]] = {}
    for task_id in sorted(replay_ids & canonical_ids):
        replay_task = replayed.tasks[task_id]
        canonical_task = canonical_by_id[task_id]
        if replay_task.status != canonical_task.status:
            mismatches[task_id] = {
                "replayed": replay_task.status,
                "canonical": canonical_task.status,
            }
    missing_in_replay = sorted(canonical_ids - replay_ids)
    missing_in_canonical = sorted(replay_ids - canonical_ids)
    matches = (
        replayed.engagement_status == canonical.status
        and not mismatches
        and not missing_in_replay
        and not missing_in_canonical
    )
    return ReplayComparison(
        matches=matches,
        replayed_engagement_status=replayed.engagement_status,
        canonical_engagement_status=canonical.status,
        task_status_mismatches=mismatches,
        missing_in_replay=missing_in_replay,
        missing_in_canonical=missing_in_canonical,
    )
