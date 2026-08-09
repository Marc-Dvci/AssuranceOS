"""Learn a company from its public footprint, correct one fact, approve the profile.

    python scripts/run_onboarding_demo.py

Ingests the six controlled public pages, proposes source-attributed facts,
records the legal-entity correction a reviewer has to make, and approves the
canonical profile.

The pages come from the published corpus by default, which keeps the run
byte-reproducible. `--live` retrieves them over the network instead, under a
collection grant that names the hosts it may reach:

    python scripts/run_onboarding_demo.py --live --allow-host asteria-demo.example         --allow-host status.asteria-demo.example
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
from assuranceos.public_sources import CollectionGrant, PublicSourceCollector  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEMO_TENANT)
    parser.add_argument(
        "--live",
        action="store_true",
        help="retrieve the pages over the network instead of reading the corpus",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="host the collection grant permits; repeat. Required with --live.",
    )
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    parser.add_argument("--ignore-robots", action="store_true")
    args = parser.parse_args()

    collector = None
    if args.live:
        if not args.allow_host:
            raise SystemExit("--live requires at least one --allow-host")
        collector = PublicSourceCollector(
            grant=CollectionGrant(
                purpose="public company intelligence for onboarding",
                allowed_hosts=frozenset(args.allow_host),
                max_bytes=args.max_bytes,
                obey_robots=not args.ignore_robots,
            )
        )

    result = run_onboarding_demo(
        database=Database(settings.database_url),
        repository_root=ROOT,
        tenant_id=args.tenant,
        collector=collector,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    print(
        f"\n{result['status']}: {result['source_snapshots']} public sources · "
        f"{result['facts_proposed']} facts proposed · "
        f"{result['facts_corrected']} corrected · canonical name {result['legal_name']!r}"
    )


if __name__ == "__main__":
    main()
