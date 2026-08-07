"""Compile signed Audit Packs into engagements, and show every way it refuses.

Registers the released packs, approves two of them, compiles two engagements from
different packs, proves the compilation is deterministic, and then attempts six
compilations that must fail — an unentitled licensed standard, criteria that do
not cover the audit period, a missing pinned control test, an unapproved pack, an
engagement that is already compiled, and a pack modified after signing.

Entirely offline and deterministic: no model is involved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from assuranceos.config import settings
from assuranceos.db.session import Database
from assuranceos.standards.demo import run_pack_compiler_demo

ROOT = Path(__file__).resolve().parents[1]


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
        result = run_pack_compiler_demo(database=database, repository_root=ROOT)
    finally:
        database.dispose()
    print(json.dumps(result, indent=2, sort_keys=False))


if __name__ == "__main__":
    main()
