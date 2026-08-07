"""Score the Asteria audit universe and derive a plan under real capacity limits.

Shows the two rules that keep risk ratings honest — an untested control reduces
nothing, and low confidence raises audit priority rather than lowering it — then
plans under a capacity that cannot fit everything, reports what was excluded and
what that leaves uncovered, recomputes under a budget cut without recording
anything, and has a person approve the plan and thereby accept its residual.

Entirely offline and deterministic: no model is involved.
"""

from __future__ import annotations

import argparse
import json

from assuranceos.config import settings
from assuranceos.db.session import Database
from assuranceos.portfolio.demo import run_portfolio_demo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="target database; defaults to the configured one",
    )
    args = parser.parse_args()

    database = Database(args.database_url)
    try:
        result = run_portfolio_demo(database=database)
    finally:
        database.dispose()
    print(json.dumps(result, indent=2, sort_keys=False))


if __name__ == "__main__":
    main()
