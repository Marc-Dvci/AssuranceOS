from pathlib import Path

from assuranceos.db import Database
from assuranceos.scheduling.demo import run_scheduler_demo


def test_scheduler_demo_launches_orchestrated_engagement(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = Database.from_sqlite_path(tmp_path / "scheduler-demo.db")
    database.create_schema()
    try:
        result = run_scheduler_demo(
            database=database,
            workflow_path=root / "examples/workflows/software-change-management.json",
        )
    finally:
        database.dispose()

    assert result["evaluation"]["launched"] == 1
    assert result["occurrence"]["status"] == "launched"
    assert result["engagement_status"] == "running"
    assert result["ready_tasks"] == ["collect-evidence"]
    assert len(result["future_occurrences"]) >= 3
