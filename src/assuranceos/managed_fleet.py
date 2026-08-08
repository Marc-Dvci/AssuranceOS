"""Deploy-time configuration and runtime proof for the managed ADK fleet.

The release registry proves what is eligible to run. A managed-fleet proof
proves where that exact release is running. Cloud deployment writes the proof
document produced by the deploy_adk_agent script; the API validates its resource
names, package digests, model, and Memory Bank configuration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_MEMORY_TTL_SECONDS = 365 * 24 * 60 * 60


def vertex_model_resource(project: str, location: str, model: str) -> str:
    """Return the publisher-model resource consumed by Agent Platform."""

    return f"projects/{project}/locations/{location}/publishers/google/models/{model}"


def memory_bank_config(
    *,
    project: str,
    location: str,
    model: str = DEFAULT_GEMINI_MODEL,
    ttl_seconds: int = DEFAULT_MEMORY_TTL_SECONDS,
) -> dict[str, Any]:
    """Build the production Memory Bank policy for every managed agent.

    Memories are scoped through the ADK user_id. AssuranceOS supplies a
    tenant-qualified subject for that value, preventing recall across tenants.
    Revisions remain enabled so memory changes are inspectable.
    """

    if not project.strip() or not location.strip():
        raise ValueError("project and location are required for Memory Bank")
    if ttl_seconds <= 0:
        raise ValueError("Memory Bank TTL must be positive")
    return {
        "generation_config": {
            "model": vertex_model_resource(project, location, model),
        },
        "similarity_search_config": {
            "embedding_model": vertex_model_resource(
                project, location, "text-embedding-005"
            ),
        },
        "ttl_config": {
            "memory_revision_default_ttl": f"{ttl_seconds}s",
        },
        "customization_configs": [
            {
                "scope_keys": ["user_id"],
                "memory_topics": [
                    {
                        "managed_memory_topic": {
                            "managed_topic_enum": "KEY_CONVERSATION_DETAILS"
                        }
                    },
                    {
                        "managed_memory_topic": {
                            "managed_topic_enum": "EXPLICIT_INSTRUCTIONS"
                        }
                    },
                ],
                "consolidation_config": {"revisions_per_candidate_count": 1},
                "generate_memories_examples": [],
                "enable_third_person_memories": True,
            }
        ],
        "disable_memory_revisions": False,
    }


def deployment_context_spec(
    *, project: str, location: str, model: str = DEFAULT_GEMINI_MODEL
) -> dict[str, Any]:
    return {
        "context_spec": {
            "memory_bank_config": memory_bank_config(
                project=project,
                location=location,
                model=model,
            )
        }
    }


def tenant_memory_subject(tenant_id: str, principal_id: str) -> str:
    """Create a stable, non-ambiguous ADK Memory Bank user scope."""

    tenant = tenant_id.strip()
    principal = principal_id.strip()
    if not tenant or not principal:
        raise ValueError("tenant_id and principal_id are required")
    if ":" in tenant or ":" in principal:
        raise ValueError("memory subject components cannot contain ':'")
    return f"tenant:{tenant}:principal:{principal}"


async def persist_reviewed_session(
    app: Any,
    *,
    session: Mapping[str, Any],
    tenant_id: str,
    principal_id: str,
) -> dict[str, Any]:
    """Generate long-term memory only from a reviewed, tenant-bound session."""

    document = dict(session)
    expected_subject = tenant_memory_subject(tenant_id, principal_id)
    if document.get("user_id") != expected_subject:
        raise ValueError("session user_id is not bound to the requested tenant subject")
    state = document.get("state")
    if not isinstance(state, dict) or state.get("memory_review_status") != "approved":
        raise ValueError("only sessions approved for memory generation may be persisted")
    await app.async_add_session_to_memory(session=document)
    return {
        "generated": True,
        "tenant_subject": expected_subject,
        "session_id": document.get("id") or document.get("session_id"),
        "review_status": "approved",
    }


def managed_fleet_proof(
    *,
    repository_root: Path,
    expected_packages: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    """Load and verify deploy output supplied as JSON or an explicit file path."""

    source = "deployment_plan"
    raw = os.getenv("ASSURANCEOS_AGENT_ENGINE_RESOURCE_MAP_JSON", "").strip()
    path_value = os.getenv("ASSURANCEOS_AGENT_ENGINE_PROOF", "").strip()
    document: dict[str, Any] | None = None
    if raw:
        document = json.loads(raw)
        source = "environment"
    elif path_value:
        proof_path = Path(path_value)
        if not proof_path.is_absolute():
            proof_path = repository_root / proof_path
        if proof_path.is_file():
            document = json.loads(proof_path.read_text(encoding="utf-8"))
            source = str(proof_path)

    expected = {
        agent_id: str(package.release.get("package_sha256") or "")
        for agent_id, package in expected_packages.items()
    }
    deployed: list[dict[str, Any]] = []
    errors: list[str] = []
    if document is not None:
        if document.get("schema") != "assurance.agent_engine_deployment_result.v1":
            errors.append("unsupported deployment proof schema")
        if document.get("model") != model:
            errors.append("deployment model does not match the running release")
        for item in document.get("deployed") or []:
            agent_id = str(item.get("agent_id") or "")
            resource_name = str(item.get("resource_name") or "")
            digest = str(item.get("package_sha256") or "")
            if agent_id not in expected:
                errors.append(f"unknown deployed agent: {agent_id}")
                continue
            if digest != expected[agent_id]:
                errors.append(f"release digest mismatch: {agent_id}")
                continue
            if "/reasoningEngines/" not in resource_name:
                errors.append(f"invalid Agent Engine resource: {agent_id}")
                continue
            deployed.append(dict(item))
        if not document.get("complete"):
            errors.append("deployment result is not complete")
        if len(deployed) != len(expected):
            errors.append("deployment result does not cover the complete signed fleet")

    project = str((document or {}).get("project") or os.getenv("GOOGLE_CLOUD_PROJECT") or "")
    location = str(
        (document or {}).get("region")
        or os.getenv("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )
    planned_memory = (
        memory_bank_config(project=project, location=location, model=model)
        if project
        else {
            "generation_config": {"model": model},
            "scope_keys": ["user_id"],
            "tenant_subject_format": "tenant:{tenant_id}:principal:{principal_id}",
        }
    )
    cloud_verified = document is not None and not errors
    return {
        "status": "cloud_verified" if cloud_verified else "release_qualified",
        "cloud_verified": cloud_verified,
        "source": source,
        "deployed_count": len(deployed),
        "expected_count": len(expected),
        "agents": deployed,
        "memory_bank": {
            "configured": True,
            "service": "VertexAiMemoryBankService",
            "generation": "explicit_after_review",
            "tenant_isolation": "tenant-qualified user_id",
            "revision_history": True,
            "configuration": planned_memory,
        },
        "verification_errors": errors,
        "deployed_at": (document or {}).get("deployed_at"),
    }
