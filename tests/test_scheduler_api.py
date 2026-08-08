from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

import assuranceos.api as api
from assuranceos.db import Database
from assuranceos.orchestration import Orchestrator, WorkflowDefinition
from assuranceos.scheduling.demo import (
    SCHEDULER_DEMO_NOW,
    SCHEDULER_DEMO_SCHEDULE_ID,
    SCHEDULER_DEMO_TENANT_ID,
    _reset_and_seed,
)
from assuranceos.scheduling.service import AuditScheduler


def test_scheduler_http_contracts(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    workflow = WorkflowDefinition.model_validate(
        json.loads(
            (root / "examples/workflows/software-change-management.json").read_text(
                encoding="utf-8"
            )
        )
    )
    database = Database.from_sqlite_path(tmp_path / "api-scheduler.db")
    database.create_schema()
    _reset_and_seed(database, workflow, SCHEDULER_DEMO_TENANT_ID)

    def clock() -> datetime:
        return SCHEDULER_DEMO_NOW

    orchestrator = Orchestrator(database, clock=clock)
    scheduler = AuditScheduler(database, clock=clock, orchestrator=orchestrator)
    monkeypatch.setattr(api, "database", database)
    monkeypatch.setattr(api, "orchestrator", orchestrator)
    monkeypatch.setattr(api, "scheduler", scheduler)

    try:
        client = TestClient(api.app)
        evaluation = client.post(
            f"/api/v1/tenants/{SCHEDULER_DEMO_TENANT_ID}/schedules/"
            f"{SCHEDULER_DEMO_SCHEDULE_ID}/evaluate",
            json={
                "context": {
                    "connector_health": {"github": "healthy", "jira": "healthy"},
                    "available_budget_usd": 25,
                    "available_competencies": [
                        "internal_auditor",
                        "technology_auditor",
                    ],
                    "independence_conflicts": [],
                    "attributes": {},
                }
            },
        )
        assert evaluation.status_code == 200
        assert evaluation.json()["launched"] == 1

        occurrences = client.get(
            f"/api/v1/tenants/{SCHEDULER_DEMO_TENANT_ID}/schedules/"
            f"{SCHEDULER_DEMO_SCHEDULE_ID}/occurrences"
        )
        assert occurrences.status_code == 200
        occurrence = occurrences.json()[0]
        assert occurrence["status"] == "launched"

        single = client.get(
            f"/api/v1/tenants/{SCHEDULER_DEMO_TENANT_ID}/occurrences/"
            f"{occurrence['occurrence_id']}"
        )
        assert single.status_code == 200
        assert single.json()["engagement_id"] == occurrence["engagement_id"]

        simulation = client.post(
            f"/api/v1/tenants/{SCHEDULER_DEMO_TENANT_ID}/schedules/"
            f"{SCHEDULER_DEMO_SCHEDULE_ID}/simulate",
            json={
                "window_start": "2026-08-05T00:00:00Z",
                "window_end": "2027-08-07T00:00:00Z",
                "limit": 20,
            },
        )
        assert simulation.status_code == 200
        assert len(simulation.json()) == 3
    finally:
        database.dispose()
