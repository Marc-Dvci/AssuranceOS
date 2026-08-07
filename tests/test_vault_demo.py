from pathlib import Path

from assuranceos.db.session import Database
from assuranceos.vault.demo import run_evidence_vault_demo


def test_evidence_vault_demo(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = Database.from_sqlite_path(tmp_path / "demo.db")
    database.create_schema()
    try:
        result = run_evidence_vault_demo(
            database=database,
            object_root=tmp_path / "objects",
            demo_root=root / "demo/asteria",
            export_path=tmp_path / "exports/demo.zip",
        )
    finally:
        database.dispose()

    assert result["acquired_count"] == 4
    assert result["lineage_nodes"] == 2
    assert result["lineage_edges"] == 1
    assert result["custody_valid"] is True
    assert result["integrity_verified"] == 5
    assert result["export_valid"] is True
    assert result["export_evidence_count"] == 2
