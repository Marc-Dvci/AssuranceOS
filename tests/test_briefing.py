"""What the agent is told, and what it is refused for.

Two things are under test here and they are deliberately separate. The briefing
is a pure composition, so it is tested as one: same inputs, same text, no
database. The auto-review is a gate on the reply, so it is tested against the
runtime that enforces it.

The property that matters most is the one in
:func:`test_a_different_company_produces_a_different_briefing`. If the same pack
step briefs identically for two different companies, then nothing about the
adaptation is real and the organization section is decoration.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from assuranceos.briefing import (
    DEFINITION_OF_DONE,
    ROLE_ADDENDA,
    OrganizationBrief,
    OrganizationFactView,
    brief_from_lease,
    render_briefing,
)
from assuranceos.governance.runtime import QualityContext

PACK_STEP = {
    "pack_reference": "software-change-management@2.0.0",
    "objective": "Assess whether production software changes are authorized.",
    "action": "Execute the released deterministic test over the complete population.",
    "criteria_detail": [
        {
            "criteria_id": "AST-POL-SCM-02",
            "text": "Every production change must carry an independent approval.",
            "citation": "Asteria change policy v4, section 3.4",
        }
    ],
    "control": {
        "control_id": "SCM-01",
        "risk": "An unreviewed change reaches production.",
        "expected": "Every in-scope merge has an approved ticket.",
    },
    "quality_rules": ["population must reconcile"],
    "control_test": "SCM-01@2.0.0",
    "depends_on": ["capture-change-evidence"],
}

ENGAGEMENT = {
    "code": "SCM-2026-07",
    "title": "Software change management",
    "period": (date(2026, 7, 1), date(2026, 7, 31)),
}


def lease(**overrides) -> SimpleNamespace:
    fields = {
        "task_key": "execute-population-test",
        "task_type": "control_test",
        "assigned_agent_role": "operating-effectiveness",
        "engagement_id": "eng_scm",
        "execution_policy": dict(PACK_STEP),
        "human_gate": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def company(name: str, industry: str, **overrides) -> OrganizationBrief:
    fields = {
        "legal_name": name,
        "industry": industry,
        "headquarters_country": "FR",
        "primary_domain": "example.test",
        "status": "canonical",
        "facts": (
            OrganizationFactView("public.processes_personal_data", True, "inference"),
        ),
    }
    fields.update(overrides)
    return OrganizationBrief(**fields)


def brief_text(**overrides) -> str:
    organization = overrides.pop("organization", company("Asteria Systems SAS", "Invoice SaaS"))
    return render_briefing(
        brief_from_lease(lease(**overrides), organization=organization, engagement=ENGAGEMENT)
    )


# -- the briefing ----------------------------------------------------------------


def test_the_pack_step_reaches_the_agent_in_full():
    """Action, objective, criteria text, control expectation, period, methodology.

    Every one of these was already in canonical state before this module existed
    and none of it reached a prompt, which is the whole defect being fixed.
    """
    text = brief_text()
    assert "Execute the released deterministic test over the complete population." in text
    assert "Assess whether production software changes are authorized." in text
    assert "AST-POL-SCM-02" in text
    assert "Every production change must carry an independent approval." in text
    assert "Asteria change policy v4, section 3.4" in text
    assert "Every in-scope merge has an approved ticket." in text
    assert "2026-07-01 to 2026-07-31" in text
    assert "software-change-management@2.0.0" in text


def test_a_different_company_produces_a_different_briefing():
    """The adaptation is structural, not a claim.

    Same pack, same step, same period — two companies, two briefings. If this
    ever passes trivially the organization section has stopped being read.
    """
    asteria = brief_text(organization=company("Asteria Systems SAS", "Invoice automation SaaS"))
    northwind = brief_text(
        organization=company(
            "Northwind Freight GmbH",
            "Cross-border logistics",
            headquarters_country="DE",
            facts=(
                OrganizationFactView(
                    "public.security_commitments", ["ISO 28000"], "observed"
                ),
            ),
        )
    )
    assert asteria != northwind
    assert "Invoice automation SaaS" in asteria and "Invoice automation SaaS" not in northwind
    assert "Cross-border logistics" in northwind
    assert "ISO 28000" in northwind


def test_a_different_task_type_changes_what_done_means():
    """The audit type adapts through the pack, the step type through the contract."""
    testing = brief_text(task_type="control_test")
    collection = brief_text(task_type="connector_collection", assigned_agent_role="evidence-custodian")
    assert DEFINITION_OF_DONE["control_test"][0][:40] in testing
    assert DEFINITION_OF_DONE["connector_collection"][0][:40] in collection
    assert "Custody is the task" in collection
    assert "Custody is the task" not in testing


def test_a_review_role_is_told_it_is_a_review_role():
    text = brief_text(assigned_agent_role="skeptic", task_type="agent")
    assert ROLE_ADDENDA["skeptic"][0][:40] in text
    assert "fail the exception" in text


def test_an_inference_is_labelled_as_one():
    """A model must not be able to cite the platform's guess as the company's word."""
    text = brief_text()
    assert "[inference]" in text
    assert "not something the company stated" in text


def test_a_human_gate_is_stated_as_a_stopping_point():
    text = brief_text(human_gate="finding_approval")
    assert "finding approval" in text
    assert "do not act as though it has been" in text


def test_an_unonboarded_tenant_is_told_it_knows_nothing():
    """Silence about the company must read as silence, never as a blank slate.

    An agent given no profile and no warning fills the gap from the model's
    priors, and a plausible invented industry is indistinguishable in the output
    from a real one.
    """
    text = render_briefing(
        brief_from_lease(lease(), organization=None, engagement=ENGAGEMENT)
    )
    assert "No approved organization profile" in text
    assert "Do not assume an industry" in text


def test_an_unknown_task_type_still_gets_a_contract():
    text = brief_text(task_type="something-new")
    assert DEFINITION_OF_DONE["agent"][0] in text


def test_only_the_criteria_codes_still_brief_something():
    """A task compiled before criteria_detail existed must not brief an empty list."""
    policy = {key: value for key, value in PACK_STEP.items() if key != "criteria_detail"}
    policy["criteria"] = ["AST-POL-SCM-02"]
    text = render_briefing(
        brief_from_lease(
            lease(execution_policy=policy), organization=None, engagement=ENGAGEMENT
        )
    )
    assert "AST-POL-SCM-02" in text


# -- the auto-review -------------------------------------------------------------


def review(reply: dict, observations=(), quality: QualityContext | None = None):
    from assuranceos.governance.runtime import GovernedAgentRuntime

    return GovernedAgentRuntime._review_output(reply, list(observations), quality)


def test_ran_the_signed_procedure(observation_of_a_run=None):
    ok, problem = review(
        {"conclusion": "ineffective", "requires_human_approval": True},
        observations=[
            {
                "tool": "tests.execute",
                "outcome": "allowed",
                "result": {"exception_count": 3, "population_count": 44},
            }
        ],
        quality=QualityContext(required_control_test="SCM-01@2.0.0"),
    )
    assert ok, problem


def test_a_conclusion_without_the_signed_procedure_is_refused():
    """The pack pinned a procedure precisely so the answer does not depend on the model."""
    ok, problem = review(
        {"conclusion": "effective", "requires_human_approval": False},
        quality=QualityContext(required_control_test="SCM-01@2.0.0"),
    )
    assert not ok
    assert "SCM-01@2.0.0" in problem and "never executed" in problem


def test_insufficient_evidence_is_always_an_available_answer():
    ok, problem = review(
        {"conclusion": "insufficient_evidence"},
        quality=QualityContext(required_control_test="SCM-01@2.0.0"),
    )
    assert ok, problem


def test_effective_over_a_run_that_returned_exceptions_is_refused():
    ok, problem = review(
        {"conclusion": "effective"},
        observations=[
            {"tool": "tests.execute", "outcome": "allowed", "result": {"exception_count": 3}}
        ],
        quality=QualityContext(),
    )
    assert not ok
    assert "3 exception" in problem


def test_an_adverse_conclusion_must_be_a_proposal():
    ok, problem = review(
        {"conclusion": "ineffective", "requires_human_approval": False},
        observations=[
            {"tool": "tests.execute", "outcome": "allowed", "result": {"exception_count": 1}}
        ],
        quality=QualityContext(),
    )
    assert not ok
    assert "requires_human_approval" in problem


def test_a_denied_tool_call_does_not_count_as_having_run_the_procedure():
    """The denial is an observation too, and it is not a result."""
    ok, problem = review(
        {"conclusion": "ineffective", "requires_human_approval": True},
        observations=[{"tool": "tests.execute", "outcome": "denied", "rendered": "refused"}],
        quality=QualityContext(required_control_test="SCM-01@2.0.0"),
    )
    assert not ok
    assert "never executed" in problem


def test_no_quality_context_leaves_the_gate_where_it_was():
    """Tasks with no compiled methodology keep the citation gate and nothing more."""
    ok, problem = review({"conclusion": "effective"}, quality=None)
    assert ok, problem


@pytest.mark.parametrize("reply", ["{}", json.dumps({"conclusion": ""})])
def test_a_reply_with_no_conclusion_is_left_to_the_schema_gate(reply):
    ok, _ = review(json.loads(reply), quality=QualityContext())
    assert ok


# -- what the agent is offered ---------------------------------------------------


def test_only_callable_tools_reach_the_envelope():
    """A declared tool with no handler must not appear in the tool catalogue.

    The fleet's packages name roughly seventy tools between them and this
    deployment implements seventeen. Offering the rest puts the model in a
    position where asking for a real part of its mandate produces a denial that
    reads, in the decision log, exactly like a control firing.
    """
    from assuranceos.governance.worker import GovernedAgentTaskHandler

    class _Package:
        agent_id = "operating-effectiveness"
        tools = {"tools": [{"name": "tests.execute"}, {"name": "request.create"}]}

    class _Gateway:
        def registered_tools(self, role):
            assert role == "operating-effectiveness"
            return ["tests.execute"]

    handler = object.__new__(GovernedAgentTaskHandler)
    handler.gateway = _Gateway()
    assert handler._callable_tools(_Package()) == ["tests.execute"]


def test_a_gateway_with_nothing_bound_leaves_the_declaration_alone():
    """Narrowing to an empty set would disarm the agent rather than describe it."""
    from assuranceos.governance.worker import GovernedAgentTaskHandler

    class _Package:
        agent_id = "skeptic"
        tools = {"tools": [{"name": "claims.read"}, {"name": "period.validate"}]}

    class _Gateway:
        def registered_tools(self, role):
            return []

    handler = object.__new__(GovernedAgentTaskHandler)
    handler.gateway = _Gateway()
    assert handler._callable_tools(_Package()) == ["claims.read", "period.validate"]
