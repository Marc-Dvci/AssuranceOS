from pathlib import Path

from assuranceos.control_testing.demo import run_control_test_demo
from assuranceos.db.session import Database


def test_control_test_demo_runs_both_released_procedures(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = Database.from_sqlite_path(tmp_path / "demo.db")
    database.create_schema()
    try:
        result = run_control_test_demo(database, root)
        assert {item["test_id"] for item in result["released_tests"]} == {"SCM-01", "IAM-01"}
        assert len(result["runs"]) == 2
        assert all(item["exception_count"] == 1 for item in result["runs"])
    finally:
        database.dispose()
