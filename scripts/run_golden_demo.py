from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.config import settings  # noqa: E402
from assuranceos.demo import run_golden_engagement  # noqa: E402
from assuranceos.ledger import AuditLedger  # noqa: E402

def main() -> None:
    result = run_golden_engagement(ROOT / "demo/asteria", AuditLedger(settings.database_url))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
