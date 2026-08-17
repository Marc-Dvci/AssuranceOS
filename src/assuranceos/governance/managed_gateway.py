"""Google-managed Agent Gateway read-back, for the network layer of the fleet.

Two enforcement points sit in front of a deployed agent and they answer
different questions. Google's Agent Gateway governs *where* a deployed agent
may send HTTP traffic: it default-denies destinations absent from the Agent
Registry and terminates the connection in Google's own network, outside the
agent's process. The AssuranceOS gateway governs *what a bounded task is
authorised to do*, which is a question about audit authority rather than about
a destination. A network policy has no way to express that a retest must be
performed by a different identity from the one that raised the finding.

Neither substitutes for the other, so this module verifies the managed gateway
and reports it as its own layer rather than folding it into the application
gateway's decisions.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

GatewayTransport = Callable[[str], Mapping[str, Any]]
_GATEWAY_PATTERN = re.compile(
    r"^projects/[^/]+/locations/(?P<location>[^/]+)/agentGateways/[^/]+$"
)


def verify_agent_gateway(
    resource: str,
    *,
    bound_agents: Iterable[str],
    transport: GatewayTransport | None = None,
) -> dict[str, Any]:
    """Read a managed Agent Gateway back and return a bounded receipt.

    ``bound_agents`` names the deployed agents whose egress this deployment
    binds to the gateway. It is recorded per agent because a partly bound fleet
    is the normal state while a binding is rolled out, and reporting that as a
    fleet-wide property would be false.
    """

    name = resource.strip()
    if _GATEWAY_PATTERN.fullmatch(name) is None:
        raise ValueError(
            "Agent Gateway must match "
            "projects/{project}/locations/{location}/agentGateways/{gateway}"
        )
    agents = sorted({str(agent).strip() for agent in bound_agents if str(agent).strip()})
    if not agents:
        raise ValueError("at least one deployed agent must be bound to the gateway")

    document = dict((transport or _request)(name))
    managed = document.get("googleManaged")
    if not isinstance(managed, Mapping):
        raise RuntimeError("Agent Gateway is not a Google-managed gateway")
    path = str(managed.get("governedAccessPath") or "")
    if path != "AGENT_TO_ANYWHERE":
        raise RuntimeError(f"Agent Gateway governed access path is {path or 'missing'}")
    if str(document.get("name") or "") != name:
        raise RuntimeError("Agent Gateway read-back returned a different resource")

    protocols = document.get("protocols")
    return {
        "schema": "assurance.agent_gateway_verification.v1",
        "resource": name,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "method": "networkservices.agentGateways.get",
        "governed_access_path": "AGENT_TO_ANYWHERE",
        "protocols": sorted(str(item) for item in protocols)
        if isinstance(protocols, list)
        else [],
        "bound_agents": agents,
        # Recorded so a reader is never left to infer it: the managed gateway
        # is the network boundary, and the audit rules are enforced elsewhere.
        "authority_enforcement_point": "assuranceos_gateway",
    }


def _request(name: str) -> Mapping[str, Any]:
    try:
        import google.auth
        from google.auth.transport.requests import Request
        import httpx
    except ImportError as exc:  # pragma: no cover - cloud optional dependency
        raise RuntimeError("install the gcp-runtime extra for managed Agent Gateway") from exc

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    endpoint = f"https://networkservices.googleapis.com/v1/{name}"
    headers: dict[str, str] = {}
    credentials.before_request(Request(), "GET", endpoint, headers)
    response = httpx.get(endpoint, headers=headers, timeout=15.0)
    response.raise_for_status()
    document = response.json()
    if not isinstance(document, dict):
        raise RuntimeError("Agent Gateway response must be a JSON object")
    return document
