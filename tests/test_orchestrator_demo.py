from pathlib import Path

from assuranceos.db import Database
from assuranceos.orchestration.demo import run_orchestrator_demo


def test_orchestrator_demo_reaches_approved_report(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = Database.from_sqlite_path(tmp_path / "demo.db")
    database.create_schema()
    try:
        result = run_orchestrator_demo(
            database=database,
            demo_root=root / "demo/asteria",
            workflow_path=root / "examples/workflows/software-change-management.json",
        )
    finally:
        database.dispose()

    assert result["engagement_status"] == "completed"
    assert result["replay_matches_canonical"] is True
    assert result["test_result"]["population_count"] == 43
    assert result["test_result"]["exception_count"] == 3
    assert [item["gate"] for item in result["approvals"]] == [
        "finding_approval",
        "report_issuance",
    ]
