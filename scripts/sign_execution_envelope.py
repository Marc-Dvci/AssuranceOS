from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.execution_security import Ed25519ExecutionEnvelopeSigner  # noqa: E402
from assuranceos.models import ExecutionEnvelope  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Issue a short-lived signed execution envelope from an ExecutionEnvelope JSON file."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", default="assuranceos-execution-v1")
    parser.add_argument("--ttl-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.ttl_seconds <= 0:
        raise SystemExit("--ttl-seconds must be positive")
    envelope = ExecutionEnvelope.model_validate_json(args.input.read_text(encoding="utf-8"))
    signer = Ed25519ExecutionEnvelopeSigner.from_pem(args.private_key, key_id=args.key_id)
    signed = signer.issue(envelope, ttl=timedelta(seconds=args.ttl_seconds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(signed.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
