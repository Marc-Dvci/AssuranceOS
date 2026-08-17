"""Deploy-time configuration and runtime proof for the managed ADK fleet.

The release registry proves what is eligible to run. A managed-fleet proof
proves where that exact release is running. Cloud deployment writes the proof
document produced by the deploy_adk_agent script; the API validates its resource
names, package digests, model, and Memory Bank configuration.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
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


def _validate_model_armor_receipt(
    receipt: Any,
    *,
    template: str,
) -> list[str]:
    """Check a Model Armor verification receipt against the configured template.

    Returns the reasons the receipt is not acceptable; an empty list means the
    template was exercised on both decision paths and answered correctly.
    """

    if not isinstance(receipt, dict):
        return ["Model Armor verification receipt is missing"]
    errors: list[str] = []
    if receipt.get("schema") != "assurance.model_armor_verification.v1":
        errors.append("unsupported Model Armor verification schema")
    if receipt.get("template") != template:
        errors.append("Model Armor template does not match deployment")
    if receipt.get("safe_model_response") != "NO_MATCH_FOUND":
        errors.append("Model Armor safe-response check did not pass")
    if receipt.get("adversarial_user_prompt") != "MATCH_FOUND":
        errors.append("Model Armor adversarial check did not pass")
    if not _is_utc_timestamp(receipt.get("verified_at")):
        errors.append("Model Armor verification timestamp is invalid")
    return errors


def _load_model_armor_receipt(*, repository_root: Path) -> tuple[Any, str | None, list[str]]:
    """Load a standalone Model Armor receipt, if the operator produced one.

    Model Armor sits in the request path whether or not the fleet was ever
    deployed to Agent Engine, so its proof has to be loadable on its own. A
    guardrail that is running is reported as running.
    """

    raw = os.getenv("ASSURANCEOS_MODEL_ARMOR_PROOF_JSON", "").strip()
    path_value = os.getenv("ASSURANCEOS_MODEL_ARMOR_PROOF", "").strip()
    try:
        if raw:
            return json.loads(raw), "environment", []
        if path_value:
            proof_path = Path(path_value)
            if not proof_path.is_absolute():
                proof_path = repository_root / proof_path
            if not proof_path.is_file():
                return None, str(proof_path), ["configured Model Armor proof file does not exist"]
            return json.loads(proof_path.read_text(encoding="utf-8")), str(proof_path), []
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, path_value or "environment", [f"Model Armor proof could not be loaded: {exc}"]
    return None, None, []


def managed_fleet_proof(
    *,
    repository_root: Path,
    expected_packages: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    """Validate an Agent Engine deployment receipt against the signed fleet.

    A release-qualified package set is not a cloud deployment. ``cloud_verified``
    is therefore reserved for receipts produced after the deployment command has
    read every resource back from the Agent Engine API. The receipt is still an
    operator-supplied artifact (not a remote attestation), and that limitation is
    made explicit in the returned proof metadata.

    Model Armor is verified separately. It guards the request path of any
    deployment, so tying its status to an Agent Engine receipt would report a
    working guardrail as absent whenever the fleet runs somewhere else.
    """

    source = "deployment_plan"
    raw = os.getenv("ASSURANCEOS_AGENT_ENGINE_RESOURCE_MAP_JSON", "").strip()
    path_value = os.getenv("ASSURANCEOS_AGENT_ENGINE_PROOF", "").strip()
    document: dict[str, Any] | None = None
    load_errors: list[str] = []
    try:
        if raw:
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError("deployment proof must be a JSON object")
            document = loaded
            source = "environment"
        elif path_value:
            proof_path = Path(path_value)
            if not proof_path.is_absolute():
                proof_path = repository_root / proof_path
            source = str(proof_path)
            if not proof_path.is_file():
                load_errors.append("configured deployment proof file does not exist")
            else:
                loaded = json.loads(proof_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("deployment proof must be a JSON object")
                document = loaded
    # Each branch states the failure in the same voice as the checks below, and
    # none of them reproduces the exception's own text: this list is rendered to
    # anyone holding an evaluator token, and a parser's message carries file
    # paths and fragments of the document it choked on.
    except OSError:
        load_errors.append("deployment proof could not be read")
    except json.JSONDecodeError:
        load_errors.append("deployment proof is not valid JSON")
    except ValueError:
        load_errors.append("deployment proof must be a JSON object")

    expected = {
        agent_id: str(package.release.get("package_sha256") or "")
        for agent_id, package in expected_packages.items()
    }
    deployed: list[dict[str, Any]] = []
    errors: list[str] = list(load_errors)
    model_armor_template = os.getenv("ASSURANCEOS_MODEL_ARMOR_TEMPLATE", "").strip()
    model_armor_errors: list[str] = []
    model_armor_verified = False
    model_armor_source: str | None = None
    if document is not None:
        if document.get("schema") != "assurance.agent_engine_deployment_result.v2":
            errors.append("unsupported deployment proof schema")
        if document.get("model") != model:
            errors.append("deployment model does not match the running release")
        project = str(document.get("project") or "").strip()
        location = str(document.get("region") or "").strip()
        if not project or not location:
            errors.append("deployment project and region are required")

        verification = document.get("verification")
        if not isinstance(verification, dict):
            errors.append("live Agent Engine read-back verification is missing")
            verification = {}
        if verification.get("method") != "agentplatform.agent_engines.get":
            errors.append("deployment was not verified through Agent Engine read-back")
        if not _is_utc_timestamp(verification.get("verified_at")):
            errors.append("deployment verification timestamp is invalid")

        items = document.get("deployed")
        if not isinstance(items, list):
            errors.append("deployed resources must be a list")
            items = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                errors.append("deployed resource entry must be an object")
                continue
            agent_id = str(item.get("agent_id") or "")
            resource_name = str(item.get("resource_name") or "")
            digest = str(item.get("package_sha256") or "")
            if agent_id not in expected:
                errors.append(f"unknown deployed agent: {agent_id}")
                continue
            if agent_id in seen:
                errors.append(f"duplicate deployed agent: {agent_id}")
                continue
            seen.add(agent_id)
            if digest != expected[agent_id]:
                errors.append(f"release digest mismatch: {agent_id}")
                continue
            expected_path = f"/locations/{location}/reasoningEngines/"
            if not resource_name.startswith("projects/") or expected_path not in resource_name:
                errors.append(f"invalid Agent Engine resource: {agent_id}")
                continue
            if item.get("identity_type") != "AGENT_IDENTITY":
                errors.append(f"managed Agent Identity is not enabled: {agent_id}")
                continue
            if not _is_utc_timestamp(item.get("verified_at")):
                errors.append(f"Agent Engine read-back timestamp is invalid: {agent_id}")
                continue
            memory = item.get("memory_bank")
            if not isinstance(memory, dict) or memory.get("configured") is not True:
                errors.append(f"Memory Bank deployment is not confirmed: {agent_id}")
                continue
            deployed.append(dict(item))
        if not document.get("complete"):
            errors.append("deployment result is not complete")
        if len(deployed) != len(expected):
            errors.append("deployment result does not cover the complete signed fleet")
        if verification.get("resource_count") != len(expected):
            errors.append("verified resource count does not match the signed fleet")

    if model_armor_template:
        armor_receipt, armor_source, armor_load_errors = _load_model_armor_receipt(
            repository_root=repository_root
        )
        if armor_receipt is None and not armor_load_errors and document is not None:
            managed_services = document.get("managed_services")
            if isinstance(managed_services, dict):
                armor_receipt = managed_services.get("model_armor")
                armor_source = "agent_engine_deployment"
        model_armor_source = armor_source
        model_armor_errors = armor_load_errors + _validate_model_armor_receipt(
            armor_receipt, template=model_armor_template
        )
        model_armor_verified = not model_armor_errors

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
    proof_status = (
        "cloud_verified"
        if cloud_verified
        else "proof_invalid"
        if document is not None or load_errors
        else "release_qualified"
    )
    return {
        "status": proof_status,
        "cloud_verified": cloud_verified,
        "verification_kind": "operator_receipt_with_live_api_readback" if cloud_verified else None,
        "attestation": "not_cryptographic",
        "source": source,
        "deployed_count": len(deployed),
        "expected_count": len(expected),
        "agents": deployed,
        "memory_bank": {
            "configured": cloud_verified,
            "deployment_ready": True,
            "service": "VertexAiMemoryBankService",
            "generation": "explicit_after_review",
            "tenant_isolation": "tenant-qualified user_id",
            "revision_history": True,
            "configuration": planned_memory,
        },
        "agent_identity": {
            "configured": cloud_verified,
            "identity_type": "AGENT_IDENTITY" if cloud_verified else None,
        },
        "model_armor": {
            "configured": model_armor_verified,
            "template": model_armor_template or None,
            "source": model_armor_source,
            "independent_of_agent_engine": True,
            "verification_errors": model_armor_errors,
        },
        "verification_errors": errors,
        "deployed_at": (document or {}).get("deployed_at"),
    }


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
