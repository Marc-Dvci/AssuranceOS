from pathlib import Path

from assuranceos.connectors.demo import run_connector_demo
from assuranceos.db.session import Database
from assuranceos.vault import EvidenceVault


def test_connector_demo_collects_all_fixture_sources(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "demo.db")
    database.create_schema()
    try:
        result = run_connector_demo(
            database, EvidenceVault.local(database, tmp_path / "objects")
        )
        assert result["all_succeeded"] is True
        assert len(result["runs"]) == 4
        assert result["evidence_count"] == 6
        assert result["source_object_count"] == 6
        assert {run["stream"] for run in result["runs"]} == {
            "pull_requests",
            "issues",
            "pages",
            "files",
        }
    finally:
        database.dispose()
