from __future__ import annotations

import pytest

from assuranceos.governance.managed_gateway import verify_agent_gateway

GATEWAY = "projects/audit/locations/us-central1/agentGateways/fleet-egress"


def _gateway(**overrides) -> dict:
    document = {
        "name": GATEWAY,
        "protocols": ["MCP"],
        "googleManaged": {"governedAccessPath": "AGENT_TO_ANYWHERE"},
    }
    document.update(overrides)
    return document


def test_a_read_back_receipt_names_the_agents_it_bound():
    receipt = verify_agent_gateway(
        GATEWAY,
        bound_agents=["operating-effectiveness", "evidence-custodian"],
        transport=lambda name: _gateway(),
    )

    assert receipt["schema"] == "assurance.agent_gateway_verification.v1"
    assert receipt["method"] == "networkservices.agentGateways.get"
    assert receipt["governed_access_path"] == "AGENT_TO_ANYWHERE"
    assert receipt["bound_agents"] == ["evidence-custodian", "operating-effectiveness"]
    # Stated in the receipt itself so no reader concludes that binding the
    # network gateway moved the audit rules into Google's enforcement path.
    assert receipt["authority_enforcement_point"] == "assuranceos_gateway"


def test_a_gateway_bound_to_nothing_is_not_a_receipt():
    with pytest.raises(ValueError):
        verify_agent_gateway(GATEWAY, bound_agents=[], transport=lambda name: _gateway())


def test_an_ingress_gateway_is_refused():
    """Only egress is configured here, so a CLIENT_TO_AGENT gateway is a mistake."""

    with pytest.raises(RuntimeError):
        verify_agent_gateway(
            GATEWAY,
            bound_agents=["operating-effectiveness"],
            transport=lambda name: _gateway(
                googleManaged={"governedAccessPath": "CLIENT_TO_AGENT"}
            ),
        )


def test_a_self_managed_gateway_is_refused():
    with pytest.raises(RuntimeError):
        verify_agent_gateway(
            GATEWAY,
            bound_agents=["operating-effectiveness"],
            transport=lambda name: {"name": GATEWAY, "selfManaged": {}},
        )


def test_a_read_back_returning_another_resource_is_refused():
    with pytest.raises(RuntimeError):
        verify_agent_gateway(
            GATEWAY,
            bound_agents=["operating-effectiveness"],
            transport=lambda name: _gateway(name=GATEWAY + "-other"),
        )


def test_a_name_that_is_not_a_gateway_is_refused():
    with pytest.raises(ValueError):
        verify_agent_gateway(
            "projects/audit/locations/us-central1/reasoningEngines/1",
            bound_agents=["operating-effectiveness"],
            transport=lambda name: _gateway(),
        )
