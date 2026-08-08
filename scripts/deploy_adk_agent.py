from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.evaluation import AgentEvaluationRunner  # noqa: E402
from assuranceos.managed_fleet import deployment_context_spec, memory_bank_config  # noqa: E402
from assuranceos.registry import AgentRegistry  # noqa: E402


RUNTIME_REQUIREMENTS = [
    "google-cloud-aiplatform[agent_engines,adk]==1.153.1",
    "google-genai>=2.9,<3",
    "PyYAML>=6,<7",
    "pydantic>=2.9,<3",
    "cryptography>=50,<51",
]


def _selection(requested: list[str] | None) -> tuple[dict[str, Any], list[str]]:
    packages = AgentRegistry(ROOT / "agents").load()
    selected = sorted(requested or packages)
    unknown = sorted(set(selected) - packages.keys())
    if unknown:
        raise SystemExit(f"unknown agents: {', '.join(unknown)}")
    return packages, selected


def _deployment_plan(
    *,
    packages: dict[str, Any],
    selected: list[str],
    model: str,
    project: str | None,
    region: str,
    staging_bucket: str | None,
) -> dict[str, Any]:
    qualification = AgentEvaluationRunner(repository_root=ROOT).run(agent_ids=selected)
    if not qualification.passed:
        raise SystemExit("agent release qualification failed; deployment refused")
    return {
        "schema": "assurance.agent_engine_deployment_plan.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "region": region,
        "model": model,
        "staging_bucket": staging_bucket,
        "requirements": RUNTIME_REQUIREMENTS,
        "memory_bank": {
            "service": "VertexAiMemoryBankService",
            "generation": "explicit_after_review",
            "tenant_isolation": "tenant-qualified user_id",
            "configuration": (
                memory_bank_config(project=project, location=region, model=model)
                if project
                else {
                    "generation_config": {"model": model},
                    "scope_keys": ["user_id"],
                    "tenant_subject_format": "tenant:{tenant_id}:principal:{principal_id}",
                }
            ),
        },
        "qualification": qualification.as_dict(include_agents=False),
        "agents": [
            {
                "agent_id": agent_id,
                "display_name": packages[agent_id].manifest["display_name"],
                "version": packages[agent_id].manifest["version"],
                "package_sha256": packages[agent_id].release["package_sha256"],
                "tool_count": len(packages[agent_id].tools.get("tools", [])),
                "human_gates": packages[agent_id].manifest.get("human_gates", []),
            }
            for agent_id in selected
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qualify and register the signed AssuranceOS fleet on Vertex AI Agent Engine"
    )
    parser.add_argument(
        "--agent",
        action="append",
        help="agent id to deploy; repeat to select several (defaults to all 19)",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="validate releases and print the deployment plan without importing cloud SDKs",
    )
    parser.add_argument("--output", type=Path, help="write the plan or deployed resource map")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    packages, selected = _selection(args.agent)
    model = os.getenv("ASSURANCEOS_GEMINI_MODEL", "gemini-3.6-flash")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    staging_bucket = os.getenv("ASSURANCEOS_AGENT_ENGINE_STAGING_BUCKET")
    plan = _deployment_plan(
        packages=packages,
        selected=selected,
        model=model,
        project=project,
        region=region,
        staging_bucket=staging_bucket,
    )
    if args.plan:
        _emit(plan, args.output)
        print(
            f"Agent Engine plan ready: {len(selected)} signed agents · "
            f"{plan['qualification']['passed_cases']} release cases passed"
        )
        return

    if not project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT is required for deployment")
    if not staging_bucket:
        raise SystemExit("ASSURANCEOS_AGENT_ENGINE_STAGING_BUCKET is required for deployment")

    try:
        import agentplatform
    except ImportError as exc:
        raise SystemExit("install the agent-cloud extra: pip install -e '.[agent-cloud]'") from exc
    from assuranceos.adk import build_agent_engine_app

    client = agentplatform.Client(project=project, location=region)
    deployed: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for agent_id in selected:
        package = packages[agent_id]
        try:
            app = build_agent_engine_app(package.path, model)
            remote = client.agent_engines.create(
                agent=app,
                config={
                    "staging_bucket": staging_bucket,
                    "requirements": RUNTIME_REQUIREMENTS,
                    "extra_packages": [
                        str(package.path),
                        str(ROOT / "src" / "assuranceos"),
                    ],
                    "display_name": f"AssuranceOS · {package.manifest['display_name']}",
                    "description": str(package.manifest["mandate"]),
                    "env_vars": {
                        "ASSURANCEOS_GEMINI_MODEL": model,
                        "ASSURANCEOS_AGENT_ID": agent_id,
                        "ASSURANCEOS_AGENT_VERSION": str(package.manifest["version"]),
                        "ASSURANCEOS_AGENT_PACKAGE_SHA256": str(
                            package.release["package_sha256"]
                        ),
                    },
                    **deployment_context_spec(
                        project=project,
                        location=region,
                        model=model,
                    ),
                },
            )
            resource_name = str(
                getattr(remote, "resource_name", "")
                or getattr(getattr(remote, "api_resource", None), "name", "")
            )
            deployed.append(
                {
                    "agent_id": agent_id,
                    "version": package.manifest["version"],
                    "package_sha256": package.release["package_sha256"],
                    "resource_name": resource_name,
                    "memory_bank": {
                        "service": "VertexAiMemoryBankService",
                        "configured": True,
                        "generation": "explicit_after_review",
                    },
                }
            )
            print(f"registered {agent_id}: {resource_name}")
        except Exception as exc:
            failures.append({"agent_id": agent_id, "error": f"{type(exc).__name__}: {exc}"})
            if not args.continue_on_error:
                break

    result = {
        **plan,
        "schema": "assurance.agent_engine_deployment_result.v1",
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "deployed": deployed,
        "failures": failures,
        "complete": len(deployed) == len(selected) and not failures,
    }
    output = args.output or ROOT / "var" / "agent-engine-deployment-result.json"
    _emit(result, output)
    if not result["complete"]:
        raise SystemExit(1)


def _emit(document: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    elif document.get("schema", "").endswith("plan.v1"):
        print(encoded, end="")


if __name__ == "__main__":
    main()
