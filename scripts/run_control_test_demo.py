from __future__ import annotations

import json
from pathlib import Path

from assuranceos.config import settings
from assuranceos.control_testing.demo import run_control_test_demo
from assuranceos.db.session import Database

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    database = Database(settings.database_url)
    try:
        result = run_control_test_demo(database, ROOT)
        print(json.dumps(result, indent=2))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
