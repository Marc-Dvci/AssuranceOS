from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from assuranceos.api import app

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "api/openapi/openapi.yaml"


def rendered() -> str:
    # JSON-compatible structures keep YAML output deterministic across environments.
    return yaml.safe_dump(json.loads(json.dumps(app.openapi())), sort_keys=False, allow_unicode=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the AssuranceOS OpenAPI contract")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = rendered()
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI contract is stale; run scripts/generate_openapi.py")
        print("OpenAPI contract is current.")
        return
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {DESTINATION.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
