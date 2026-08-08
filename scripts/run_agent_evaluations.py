from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.evaluation import AgentEvaluationRunner  # noqa: E402
from assuranceos.governance.models_client import build_client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the signed AssuranceOS fleet's release qualification suites"
    )
    parser.add_argument(
        "--mode",
        choices=("contract", "local", "gemini", "vertex"),
        default="contract",
        help="contract is deterministic and offline; other modes add live model scoring",
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--agent", action="append", dest="agents")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-outputs", action="store_true")
    args = parser.parse_args()

    client = None
    if args.mode != "contract":
        client = build_client(args.mode, model=args.model, base_url=args.base_url)
    report = AgentEvaluationRunner(
        repository_root=ROOT,
        model_client=client,
    ).run(agent_ids=args.agents)
    document = report.as_dict(include_cases=True)
    if args.include_outputs:
        for agent_doc, agent in zip(document["agents"], report.agents, strict=True):
            agent_doc["cases"] = [case.as_dict(include_output=True) for case in agent.cases]
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(
        f"Agent release qualification: {'PASS' if report.passed else 'FAIL'} · "
        f"{document['passed_agents']}/{document['agent_count']} agents · "
        f"{document['passed_cases']}/{document['case_count']} cases · {report.mode} mode"
    )
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
