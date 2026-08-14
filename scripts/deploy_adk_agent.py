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
    "google-cloud-aiplatform[agent_engines,adk]>=1.163,<2",
    "google-genai>=2.9,<3",
    "PyYAML>=6,<7",
    "pydantic>=2.9,<3",
    "cryptography>=50,<51",
]


def _resource_name(resource: Any) -> str:
    return str(
        getattr(resource, "resource_name", "")
        or getattr(resource, "name", "")
        or getattr(getattr(resource, "api_resource", None), "name", "")
    )


def _agent_engine_config(
    *,
    package: Any,
    model: str,
    project: str,
    region: str,
    staging_bucket: str,
) -> dict[str, Any]:
    """Build a config accepted by the locked Agent Platform v1beta1 client."""

    return {
        "staging_bucket": staging_bucket,
        "requirements": RUNTIME_REQUIREMENTS,
        "extra_packages": [str(package.path), str(ROOT / "src" / "assuranceos")],
        "display_name": f"AssuranceOS · {package.manifest['display_name']}",
        "description": str(package.manifest["mandate"]),
        # Managed Agent Identity complements the signed in-application identity
        # that the AssuranceOS gateway verifies for each bounded task.
        "identity_type": "AGENT_IDENTITY",
        "env_vars": {
            "ASSURANCEOS_GEMINI_MODEL": model,
            "ASSURANCEOS_AGENT_ID": package.agent_id,
            "ASSURANCEOS_AGENT_VERSION": str(package.manifest["version"]),
            "ASSURANCEOS_AGENT_PACKAGE_SHA256": str(package.release["package_sha256"]),
        },
        **deployment_context_spec(project=project, location=region, model=model),
    }


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
    model_armor_template: str | None,
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
        "model_armor": {
            "configured": bool(model_armor_template),
            "template": model_armor_template,
            "verification": "required_before_deployment" if model_armor_template else None,
        },
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
    model = os.getenv("ASSURANCEOS_GEMINI_MODEL", "gemini-3.7-flash")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    staging_bucket = os.getenv("ASSURANCEOS_AGENT_ENGINE_STAGING_BUCKET")
    model_armor_template = os.getenv("ASSURANCEOS_MODEL_ARMOR_TEMPLATE", "").strip() or None
    plan = _deployment_plan(
        packages=packages,
        selected=selected,
        model=model,
        project=project,
        region=region,
        staging_bucket=staging_bucket,
        model_armor_template=model_armor_template,
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
    from assuranceos.governance.managed_armor import verify_model_armor_template

    # Managed Agent Identity is currently exposed by the v1beta1 client surface.
    client = agentplatform.Client(
        project=project,
        location=region,
        http_options={"api_version": "v1beta1"},
    )
    try:
        model_armor_verification = (
            verify_model_armor_template(model_armor_template)
            if model_armor_template
            else None
        )
    except Exception as exc:
        raise SystemExit(f"Model Armor verification failed: {type(exc).__name__}: {exc}") from exc
    deployed: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for agent_id in selected:
        package = packages[agent_id]
        try:
            app = build_agent_engine_app(package.path, model)
            remote = client.agent_engines.create(
                agent=app,
                config=_agent_engine_config(
                    package=package,
                    model=model,
                    project=project,
                    region=region,
                    staging_bucket=staging_bucket,
                ),
            )
            resource_name = _resource_name(remote)
            if not resource_name:
                raise RuntimeError("Agent Engine create response did not contain a resource name")
            confirmed = client.agent_engines.get(name=resource_name)
            if _resource_name(confirmed) != resource_name:
                raise RuntimeError("Agent Engine read-back returned a different resource")
            verified_at = datetime.now(timezone.utc).isoformat()
            deployed.append(
                {
                    "agent_id": agent_id,
                    "version": package.manifest["version"],
                    "package_sha256": package.release["package_sha256"],
                    "resource_name": resource_name,
                    "identity_type": "AGENT_IDENTITY",
                    "verified_at": verified_at,
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
        "schema": "assurance.agent_engine_deployment_result.v2",
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "deployed": deployed,
        "failures": failures,
        "complete": len(deployed) == len(selected) and not failures,
        "verification": {
            "method": "agentplatform.agent_engines.get",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "resource_count": len(deployed),
        },
        "managed_services": {
            "model_armor": model_armor_verification,
        },
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
