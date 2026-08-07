"""Try to publish six unsupportable reports, then publish a supportable one.

Each attempt carries one defect a conventional reporting layer would happily
publish: a conclusion citing nothing, one resting on unaccepted evidence, one
resting solely on evidence a guardrail flagged, one reusing another engagement's
evidence, one citing out-of-period evidence, and one with an undisclosed
contradiction. Each is refused with its own code.

The same report then renders with the defects resolved rather than removed, is
issued by a person, and is verified by recomputing its digest - including against
a copy edited after preparation.

Entirely offline and deterministic: no model is involved.
"""

from __future__ import annotations

import argparse
import json

from assuranceos.config import settings
from assuranceos.db.session import Database
from assuranceos.reporting.demo import run_reporting_demo


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
        result = run_reporting_demo(database=database)
    finally:
        database.dispose()
    print(json.dumps(result, indent=2, sort_keys=False))


if __name__ == "__main__":
    main()
