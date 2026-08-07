from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.execution_security import ExecutionEnvelopeVerifier  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a signed execution envelope.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--key-id", default="assuranceos-execution-v1")
    parser.add_argument("--expected-task-id")
    args = parser.parse_args()
    verifier = ExecutionEnvelopeVerifier({args.key_id: args.public_key.read_bytes()})
    envelope = verifier.verify(
        args.input.read_text(encoding="utf-8"),
        expected_task_id=args.expected_task_id,
    )
    print(json.dumps(envelope.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
