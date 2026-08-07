"""Run the full assurance loop: exception to closed, verified finding.

A deterministic exception becomes a proposed finding, survives a contradiction
search, passes a human gate, opens a remediation obligation exactly once,
collects closure evidence, and is verified by an independent retester.

By default the model is scripted, so the run is deterministic and offline. Pass
``--model-mode local`` to drive the same path with a local llama.cpp or
text-generation-webui server, or ``--model-mode gemini`` to use Gemini 3.5.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from assuranceos.adjudication.demo import run_assurance_loop_demo
from assuranceos.config import settings
from assuranceos.db.session import Database
from assuranceos.governance.models_client import build_client
from assuranceos.governance.telemetry import TelemetryConfig, configure_telemetry

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-mode",
        default="mock",
        choices=["mock", "local", "gemini", "vertex"],
        help="mock keeps the run deterministic; local and gemini call a real model",
    )
    parser.add_argument("--model", default=None, help="model name override")
    parser.add_argument(
        "--base-url", default=None, help="OpenAI-compatible base URL for --model-mode local"
    )
    parser.add_argument(
        "--thinking",
        default="off",
        choices=["off", "on", "server-default"],
        help=(
            "reasoning-model deliberation for --model-mode local. Structured audit "
            "output needs it off; see the note in models_client."
        ),
    )
    args = parser.parse_args()

    configure_telemetry(TelemetryConfig(environment=settings.environment))

    client = None
    if args.model_mode != "mock":
        client = build_client(
            args.model_mode,
            model=args.model or (settings.gemini_model if "gem" in args.model_mode else None),
            base_url=args.base_url,
            enable_thinking={"off": False, "on": True, "server-default": None}[args.thinking],
        )

    database = Database(settings.database_url)
    try:
        result = run_assurance_loop_demo(
            database=database, repository_root=ROOT, model_client=client
        )
    finally:
        database.dispose()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
