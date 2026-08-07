from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.config import settings  # noqa: E402
from assuranceos.db import Database  # noqa: E402
from assuranceos.vault.demo import run_evidence_vault_demo  # noqa: E402


def main() -> None:
    database = Database(settings.database_url)
    try:
        database.create_schema()
        result = run_evidence_vault_demo(
            database=database,
            object_root=settings.evidence_root,
            demo_root=settings.demo_root,
            export_path=settings.evidence_export_root / "asteria-evidence-demo.zip",
        )
        print(json.dumps(result, indent=2, default=str))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
