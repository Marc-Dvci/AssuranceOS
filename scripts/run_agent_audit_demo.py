"""Run one governed agent that actually performs the audit.

    python scripts/run_agent_audit_demo.py
    python scripts/run_agent_audit_demo.py --model-mode local \
        --base-url http://127.0.0.1:5000/v1 --model gemma-4-12b-it-IQ4_XS.gguf
    python scripts/run_agent_audit_demo.py --model-mode vertex

The agent chooses what to ask for, runs the signed control test through the
gateway, reads the exceptions it produced, and concludes. Nothing here computes
the answer on its behalf.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.agent_audit_demo import DEMO_TENANT, run_agent_audit_demo  # noqa: E402
from assuranceos.config import settings  # noqa: E402
from assuranceos.db import Database  # noqa: E402
from assuranceos.governance.models_client import (  # noqa: E402
    build_client,
    probe_context_window,
)


def _declared_window(value: str, *, client) -> int | None:
    """Resolve --context-tokens. Measuring beats being told; neither is a cap."""
    setting = str(value or "auto").strip().lower()
    if setting in {"none", "0", ""}:
        return None
    if setting == "auto":
        return probe_context_window(client) if client is not None else None
    try:
        return int(setting)
    except ValueError:
        raise SystemExit("--context-tokens must be 'auto', 'none', or a number") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-mode",
        default="mock",
        choices=["mock", "local", "gemini", "vertex"],
        help="mock replays a scripted agent; local and vertex let a real model decide",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--thinking",
        default="off",
        choices=["off", "on", "server-default"],
        help="structured audit output needs deliberation off on most local models",
    )
    parser.add_argument(
        "--context-tokens",
        default="auto",
        help=(
            "what the endpoint serves, so an oversized task is refused rather "
            "than silently trimmed. 'auto' measures it (the server does not "
            "advertise it anywhere); a number states it; 'none' declares nothing "
            "and relies on the post-call truncation check. This is never a cap "
            "the runtime imposes -- serve the largest window your hardware "
            "allows and this follows it."
        ),
    )
    parser.add_argument("--tenant", default=DEMO_TENANT)
    parser.add_argument("--max-tool-rounds", type=int, default=4)
    args = parser.parse_args()

    client = None
    if args.model_mode != "mock":
        client = build_client(
            args.model_mode,
            model=args.model or (settings.gemini_model if "gem" in args.model_mode else None),
            base_url=args.base_url,
            enable_thinking={"off": False, "on": True, "server-default": None}[args.thinking],
            context_window_tokens=_declared_window(args.context_tokens, client=None),
        )
        client.context_window_tokens = _declared_window(args.context_tokens, client=client)
        if client.context_window_tokens:
            print(f"serving {client.context_window_tokens} context tokens", flush=True)

    result = run_agent_audit_demo(
        database=Database(settings.database_url),
        repository_root=ROOT,
        model_client=client,
        tenant_id=args.tenant,
        max_tool_rounds=args.max_tool_rounds,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    matched = result["ground_truth_match"]
    print(
        f"\n{result['agent_role']} on {result['model']}: {result['status']} · "
        f"{result['tool_rounds']} tool results · conclusion {result['conclusion']!r} · "
        f"ground truth {sum(matched.values())}/{len(matched)}"
    )
    if result["status"] != "completed" or not all(matched.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
