import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.config import settings  # noqa: E402
from assuranceos.demo import run_golden_engagement  # noqa: E402
from assuranceos.ledger import AuditLedger  # noqa: E402

result = run_golden_engagement(ROOT / "demo/asteria", AuditLedger(settings.database_url))
print(f"Seeded {result['event_count']} events for {result['engagement_id']}")
