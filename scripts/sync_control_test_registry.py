from __future__ import annotations

from assuranceos.config import settings
from assuranceos.control_testing import ControlTestRegistry, ControlTestService
from assuranceos.db.session import Database


def main() -> None:
    database = Database(settings.database_url)
    try:
        registry = ControlTestRegistry(
            settings.control_test_root,
            trusted_public_key=settings.control_test_public_key.read_bytes(),
        ).load()
        inserted = ControlTestService(database, registry).synchronize_registry()
        print(f"Control-test registry synchronized; inserted={inserted}, available={len(registry.list())}")
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
