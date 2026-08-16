"""Google ADK adapter for the governed agent fleet.

An ADK ``Agent`` is a model plus a set of Python callables it may invoke. That
makes the tool list the security boundary: whatever is in it, the model can
reach. So the tools bound here are not direct implementations. Each is a thin
shim that hands the call to :class:`~assuranceos.governance.gateway.AgentGateway`,
which authenticates the workload identity, evaluates the released package policy,
screens the arguments, enforces the budget, and only then reaches a handler.

The consequence is that the ADK path and the in-process runtime share one
enforcement point rather than two implementations that must be kept in agreement.
A tool the gateway would deny is denied here too, and for the same recorded
reason.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .execution_security import ExecutionEnvelopeVerifier
from .governance.gateway import AgentGateway, GatewayDenied
from .governance.identity import AgentIdentityIssuer
from .models import ExecutionEnvelope
from .policy import PolicyGateway
from .registry import AgentPackage, AgentRegistry

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


def build_gateway_tools(
    package: AgentPackage,
    *,
    gateway: AgentGateway,
    identity_issuer: AgentIdentityIssuer,
    envelope: ExecutionEnvelope,
    tool_names: Sequence[str] | None = None,
) -> list[Any]:
    """Bind the package's declared tools as gateway-routed ADK callables.

    One workload identity is minted for the task and reused across the calls
    within it, which is what the gateway's task binding expects: the credential is
    valid for this envelope and nothing else, so it cannot be replayed onto
    another task even if the model's output leaks it.

    Each callable returns a JSON string rather than raising on denial. ADK feeds a
    tool's return value back to the model, and a denial the model can read — with
    its stage and reason — lets it choose a different, permitted action instead of
    the run simply dying. The denial is still recorded on the gateway either way.
    """
    signed_identity = identity_issuer.issue(package, envelope)
    declared = [
        str(item.get("name"))
        for item in (package.tools or {}).get("tools", [])
        if item.get("name")
    ]
    selected = list(tool_names) if tool_names is not None else declared

    def make(tool_name: str) -> Any:
        def invoke(arguments_json: str = "{}") -> str:
            try:
                parsed = json.loads(arguments_json or "{}")
            except json.JSONDecodeError:
                # Malformed arguments never reach the gateway. Reporting the shape
                # error back lets the model correct it; guessing at the intent
                # would be the system inventing an argument on its behalf.
                return json.dumps(
                    {"allowed": False, "stage": "arguments", "reason": "arguments must be a JSON object"}
                )
            if not isinstance(parsed, dict):
                return json.dumps(
                    {"allowed": False, "stage": "arguments", "reason": "arguments must be a JSON object"}
                )
            try:
                result = gateway.invoke(
                    signed_identity=signed_identity,
                    envelope=envelope,
                    package=package,
                    tool_name=tool_name,
                    arguments=parsed,
                )
            except GatewayDenied as denied:
                return json.dumps(
                    {
                        "allowed": False,
                        "stage": denied.decision.stage,
                        "reason": denied.decision.reason,
                        "decision_id": denied.decision.decision_id,
                    }
                )
            return json.dumps({"allowed": True, "result": result}, default=str)

        invoke.__name__ = tool_name.replace(".", "_")
        invoke.__doc__ = (
            f"Invoke the governed tool {tool_name!r}. Pass arguments as a JSON "
            "object string. Returns a JSON object; when 'allowed' is false the "
            "call was refused by policy and the reason explains which gate."
        )
        return invoke

    return [make(name) for name in selected]


def build_adk_agent(
    agent_dir: Path,
    model: str,
    *,
    trusted_execution_keys: Mapping[str, bytes] | None = None,
    maximum_envelope_ttl: timedelta = timedelta(hours=24),
    gateway: AgentGateway | None = None,
    identity_issuer: AgentIdentityIssuer | None = None,
    envelope: ExecutionEnvelope | None = None,
) -> Any:
    """Build a governed Google ADK Agent from a signed Agent Definition Package.

    The only authority-bearing input exposed to the model is a signed execution
    envelope. The verifier authenticates the control-plane issuer before the
    package policy is evaluated, so model-generated JSON cannot expand scope.

    Supplying ``gateway``, ``identity_issuer``, and ``envelope`` additionally binds
    the package's declared domain tools, each routed through the gateway. Without
    them the agent carries only envelope validation, which is the safe default: an
    agent with no bound gateway has nothing it is authorised to reach.
    """
    try:
        from google.adk.agents import Agent
    except ImportError as exc:  # pragma: no cover - optional integration
        raise RuntimeError("Install the agent-cloud extra: pip install -e '.[agent-cloud]'") from exc

    package = AgentRegistry(agent_dir.parent).load().get(agent_dir.name)
    if package is None:
        raise ValueError(f"agent package was not found: {agent_dir}")
    instruction = (agent_dir / "system_prompt.md").read_text(encoding="utf-8")
    # The agent is cloudpickled for Agent Engine, and a concrete Path pickles as
    # the deploying platform's flavour. Deploying from Windows therefore ships a
    # WindowsPath that the Linux runtime cannot reconstruct, and the service dies
    # while loading its own agent with "cannot instantiate 'WindowsPath'" after
    # the resource has been created and billed. Nothing reachable from the
    # pickled closure reads this path, so it travels in a platform-neutral form
    # and the deploy works from any operating system.
    package = replace(package, path=PurePosixPath(package.path.as_posix()))
    policy_gateway = PolicyGateway()
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
        decision = policy_gateway.authorize(package, envelope)
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

    tools: list[Any] = [validate_signed_execution_envelope]
    if gateway is not None and identity_issuer is not None and envelope is not None:
        tools.extend(
            build_gateway_tools(
                package,
                gateway=gateway,
                identity_issuer=identity_issuer,
                envelope=envelope,
            )
        )

    return Agent(
        name=package.agent_id.replace("-", "_"),
        model=model,
        description=str(package.manifest["mandate"]),
        instruction=instruction,
        tools=tools,
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
