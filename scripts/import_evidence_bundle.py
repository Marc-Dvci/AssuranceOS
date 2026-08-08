from __future__ import annotations

import argparse
import json
from pathlib import Path

from assuranceos.config import settings
from assuranceos.db.session import Database
from assuranceos.vault import EvidenceVault, bundle_admission_result, import_signed_bundle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and admit a sealed AssuranceOS evidence bundle"
    )
    parser.add_argument("package", type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--engagement-id")
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--classification", default="confidential")
    args = parser.parse_args()

    database = Database(settings.database_url)
    vault = EvidenceVault.local(database, settings.evidence_root)
    try:
        item, verification = import_signed_bundle(
            vault,
            package=args.package,
            tenant_id=args.tenant_id,
            actor_id=args.actor_id,
            engagement_id=args.engagement_id,
            trusted_public_keys={args.key_id: args.public_key.read_bytes()},
            classification=args.classification,
        )
        print(json.dumps(bundle_admission_result(item, verification), indent=2, sort_keys=True))
        return 0
    finally:
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
