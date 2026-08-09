"""Learn a company from its public footprint, correct one fact, approve the profile.

    python scripts/run_onboarding_demo.py

Ingests the six controlled public pages, proposes source-attributed facts,
records the legal-entity correction a reviewer has to make, and approves the
canonical profile. Nothing here fetches over the network; the pages come from the
published corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.config import settings  # noqa: E402
from assuranceos.db import Database  # noqa: E402
from assuranceos.onboarding_demo import DEMO_TENANT, run_onboarding_demo  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEMO_TENANT)
    args = parser.parse_args()

    result = run_onboarding_demo(
        database=Database(settings.database_url),
        repository_root=ROOT,
        tenant_id=args.tenant,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    print(
        f"\n{result['status']}: {result['source_snapshots']} public sources · "
        f"{result['facts_proposed']} facts proposed · "
        f"{result['facts_corrected']} corrected · canonical name {result['legal_name']!r}"
    )


if __name__ == "__main__":
    main()
