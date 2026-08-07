from pathlib import Path
from assuranceos.demo import TENANT_ID, run_golden_engagement
from assuranceos.ledger import AuditLedger


def test_golden_demo(tmp_path):
    root = Path(__file__).resolve().parents[1]
    ledger = AuditLedger(tmp_path / "ledger.db")
    result = run_golden_engagement(root / "demo/asteria", ledger)
    assert result["finding"]["status"] == "proposed"
    assert result["security_event"]["canonical_state_mutated"] is False
    assert len(ledger.list_events(TENANT_ID)) == result["event_count"]
