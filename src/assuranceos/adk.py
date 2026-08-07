from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
from typing import Any, Mapping

from .execution_security import ExecutionEnvelopeVerifier
from .policy import PolicyGateway
from .registry import AgentRegistry

_DEFAULT_EXECUTION_KEY_ID = "assuranceos-execution-v1"


def _default_execution_key(agent_dir: Path) -> tuple[str, bytes]:
    repository_root = agent_dir.resolve().parents[1]
    configured_path = os.getenv("ASSURANCEOS_EXECUTION_ENVELOPE_PUBLIC_KEY")
    key_path = (
        Path(configured_path)
        if configured_path
        else repository_root / "security" / "release-keys" / "execution-envelope-public.pem"
    )
    if not key_path.is_file():
        raise ValueError(
            "execution-envelope trust key was not found; configure "
            "ASSURANCEOS_EXECUTION_ENVELOPE_PUBLIC_KEY or pass trusted_execution_keys"
        )
    key_id = os.getenv("ASSURANCEOS_EXECUTION_SIGNING_KEY_ID", _DEFAULT_EXECUTION_KEY_ID)
    return key_id, key_path.read_bytes()


def build_adk_agent(
    agent_dir: Path,
    model: str,
    *,
    trusted_execution_keys: Mapping[str, bytes] | None = None,
    maximum_envelope_ttl: timedelta = timedelta(hours=24),
) -> Any:
    """Build a governed Google ADK Agent from a signed Agent Definition Package.

    The only authority-bearing input exposed to the model is a signed execution
    envelope. The verifier authenticates the control-plane issuer before the
    package policy is evaluated, so model-generated JSON cannot expand scope.
    """
    try:
        from google.adk.agents import Agent
    except ImportError as exc:  # pragma: no cover - optional integration
        raise RuntimeError("Install the agent-cloud extra: pip install -e '.[agent-cloud]'") from exc

    package = AgentRegistry(agent_dir.parent).load().get(agent_dir.name)
    if package is None:
        raise ValueError(f"agent package was not found: {agent_dir}")
    instruction = (agent_dir / "system_prompt.md").read_text(encoding="utf-8")
    gateway = PolicyGateway()
    if trusted_execution_keys is None:
        key_id, public_key = _default_execution_key(agent_dir)
        trusted_execution_keys = {key_id: public_key}
    # Keep PEM bytes in the closure rather than cryptography key objects. This
    # remains serializable for Agent Engine deployment and creates a verifier
    # only when the tool is invoked.
    trusted_key_material = dict(trusted_execution_keys)

    def validate_signed_execution_envelope(signed_envelope_json: str) -> dict[str, object]:
        """Authenticate bounded task authority issued by the AssuranceOS control plane."""
        verifier = ExecutionEnvelopeVerifier(
            trusted_key_material,
            maximum_ttl=maximum_envelope_ttl,
        )
        envelope = verifier.verify(signed_envelope_json)
        decision = gateway.authorize(package, envelope)
        if not decision.allowed:
            raise ValueError(decision.reason)
        return {
            "authorized": True,
            "task_id": envelope.task_id,
            "engagement_id": envelope.engagement_id,
            "tenant_id": envelope.tenant_id,
            "agent_role": envelope.agent_role,
            "agent_version": envelope.agent_version,
            "allowed_tools": sorted(envelope.allowed_tools),
            "allowed_evidence_scopes": sorted(envelope.allowed_evidence_scopes),
            "deadline": envelope.deadline.isoformat() if envelope.deadline else None,
            "human_gate": envelope.human_gate,
        }

    return Agent(
        name=package.agent_id.replace("-", "_"),
        model=model,
        description=str(package.manifest["mandate"]),
        instruction=instruction,
        tools=[validate_signed_execution_envelope],
    )


def build_agent_engine_app(
    agent_dir: Path,
    model: str,
    *,
    trusted_execution_keys: Mapping[str, bytes] | None = None,
    maximum_envelope_ttl: timedelta = timedelta(hours=24),
) -> Any:
    try:
        from vertexai.agent_engines import AdkApp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the agent-cloud extra: pip install -e '.[agent-cloud]'") from exc
    return AdkApp(
        agent=build_adk_agent(
            agent_dir,
            model,
            trusted_execution_keys=trusted_execution_keys,
            maximum_envelope_ttl=maximum_envelope_ttl,
        ),
        enable_tracing=True,
    )
