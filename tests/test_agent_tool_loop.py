"""The agent loop, the context guards, and the domain tools behind them.

These cover the difference between an agent that requests tools and one that can
use them: whether results come back into the prompt, whether a conclusion drawn
from a truncated prompt can reach canonical state, and whether a tool can be
called without the contract it needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.governance.armor import ModelArmor
from assuranceos.governance.domain_tools import (
    DomainToolContext,
    DomainToolError,
    build_domain_tools,
    register_domain_tools,
    unimplemented_tools,
)
from assuranceos.governance.gateway import AgentGateway, BoundedTool
from assuranceos.governance.identity import AgentIdentityIssuer, AgentIdentityVerifier
from assuranceos.governance.models_client import ModelResponse
from assuranceos.governance.runtime import (
    EvidenceItem,
    GovernedAgentRuntime,
    estimate_tokens,
    token_floor,
)
from assuranceos.models import ExecutionEnvelope
from assuranceos.registry import AgentRegistry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def package():
    return AgentRegistry(ROOT / "agents").load()["operating-effectiveness"]


@pytest.fixture
def envelope(package):
    # The envelope must carry at least the package's prohibitions: granting a
    # weaker set is itself a policy denial, and one that masks whatever the test
    # was actually about.
    return ExecutionEnvelope(
        task_id="tsk_loop",
        engagement_id="eng_loop",
        tenant_id="tnt_loop",
        agent_role="operating-effectiveness",
        agent_version="0.8.0",
        purpose="operating effectiveness",
        allowed_evidence_scopes=["engagement"],
        allowed_tools=["evidence.query", "tests.execute"],
        forbidden_actions=list((package.policy or {}).get("forbidden_actions", [])),
        model_policy="flash",
    )


class Replies:
    """A client returning queued replies and remembering every prompt it saw."""

    model_name = "queued"

    def __init__(self, replies, input_tokens=None):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self._input_tokens = input_tokens

    def generate(self, *, system_instruction, prompt, temperature=0.0, max_output_tokens=1024):
        self.prompts.append(prompt)
        text = self.replies.pop(0) if self.replies else "{}"
        return ModelResponse(
            text=text,
            input_tokens=(
                self._input_tokens if self._input_tokens is not None else len(prompt.split())
            ),
            output_tokens=len(text.split()),
            model=self.model_name,
            finish_reason="stop",
        )


def build_runtime(client, *, tools=None, **kwargs):
    key = Ed25519PrivateKey.generate()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    gateway = AgentGateway(
        identity_verifier=AgentIdentityVerifier({"loop": public_pem}), armor=ModelArmor()
    )
    for tool in tools or []:
        gateway.register_tool("operating-effectiveness", tool)
    runtime = GovernedAgentRuntime(
        gateway=gateway,
        identity_issuer=AgentIdentityIssuer(private_key=key, key_id="loop"),
        model_client=client,
        **kwargs,
    )
    return runtime, gateway


GATHER = json.dumps(
    {"next_action": "use_tools", "tool_calls": [{"tool": "evidence.query", "arguments": {}}]}
)


def conclude(evidence_ids):
    return json.dumps(
        {
            "conclusion": "ineffective",
            "summary": "Changes merged without approval.",
            "evidence_ids": evidence_ids,
            "tool_calls": [],
            "requires_human_approval": True,
        }
    )


def test_tool_results_reach_the_next_prompt(package, envelope):
    """The loop's whole purpose: an agent that asks gets an answer it can read."""
    tool = BoundedTool(
        "evidence.query",
        lambda **_: {"evidence": [{"evidence_id": "ev_found", "source_type": "github"}]},
        description="{} - all optional",
    )
    client = Replies([GATHER, conclude(["ev_found"])])
    runtime, _ = build_runtime(client, tools=[tool])

    result = runtime.run(package=package, envelope=envelope, instruction="Test SCM-01.")

    assert result.succeeded
    assert len(client.prompts) == 2
    assert "ev_found" in client.prompts[1]
    assert result.tool_rounds == 2
    assert [item["tool"] for item in result.observations] == ["evidence.query"]


def test_evidence_found_through_a_tool_is_citable(package, envelope):
    """Restricting citations to the handed-in set would make the loop pointless."""
    tool = BoundedTool(
        "evidence.query",
        lambda **_: {"evidence": [{"evidence_id": "ev_discovered"}]},
    )
    runtime, _ = build_runtime(Replies([GATHER, conclude(["ev_discovered"])]), tools=[tool])

    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction="Test SCM-01.",
        evidence=[EvidenceItem("ev_supplied", "confluence", "policy text")],
    )

    assert result.succeeded


def test_a_denial_is_explained_back_to_the_model(package, envelope):
    """An agent refused in silence repeats the same call until the budget ends."""
    runtime, _ = build_runtime(Replies([GATHER, conclude([])]))

    result = runtime.run(package=package, envelope=envelope, instruction="Test SCM-01.")

    assert result.denials
    assert result.observations[0]["outcome"] == "denied"
    assert "no bound handler" in result.observations[0]["rendered"]


def test_an_identical_call_is_not_executed_twice(package, envelope):
    """A concluding reply lists what it used; that is a citation, not a request."""
    calls: list[dict] = []

    def handler(*, arguments, identity, envelope):
        calls.append(dict(arguments))
        return {"evidence": []}

    tool = BoundedTool("evidence.query", handler)
    repeat = json.dumps(
        {
            "conclusion": "ineffective",
            "summary": "Changes merged without approval.",
            "evidence_ids": ["ev_x"],
            "tool_calls": [{"tool": "evidence.query", "arguments": {}}],
            "requires_human_approval": True,
        }
    )
    runtime, _ = build_runtime(Replies([GATHER, repeat]), tools=[tool])

    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction="Test SCM-01.",
        evidence=[EvidenceItem("ev_x", "github", "merged")],
    )

    assert len(calls) == 1
    assert [item["outcome"] for item in result.observations] == ["allowed", "repeated"]


def test_an_unresolvable_citation_gets_one_bounded_correction(package, envelope):
    """gemma-4-12b cited `Evd_68bd...` for `evd_68bd...`; the conclusion was sound."""
    runtime, _ = build_runtime(Replies([conclude(["EV_TYPO"]), conclude(["ev_real"])]))

    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction="Test SCM-01.",
        evidence=[EvidenceItem("ev_real", "github", "merged without approval")],
    )

    assert result.succeeded
    assert result.observations[-1]["outcome"] == "rejected"


def test_a_correction_that_fails_reports_the_original_refusal(package, envelope):
    """The second failure is whatever the model degenerated into, not the reason."""
    runtime, _ = build_runtime(Replies([conclude(["EV_TYPO"]), "{}"]))

    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction="Test SCM-01.",
        evidence=[EvidenceItem("ev_real", "github", "merged")],
    )

    assert result.status == "schema_invalid"
    assert "never supplied" in result.summary
    assert "correction was attempted" in result.summary


def test_evidence_that_cannot_fit_is_refused_rather_than_trimmed(package, envelope):
    """A conclusion from the surviving rows is a different conclusion, not a weaker one."""
    runtime, _ = build_runtime(
        Replies([conclude(["ev_big"])]), context_window_tokens=500
    )

    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction="Test SCM-01.",
        evidence=[EvidenceItem("ev_big", "github", "merged without approval. " * 2000)],
    )

    assert result.status == "context_exceeded"
    assert result.output is None
    assert "Nothing was trimmed to fit" in result.summary
    assert result.context_window_tokens == 500


def test_a_server_that_read_less_than_it_was_sent_fails_closed(package, envelope):
    """Measured live: 51,909 tokens sent, 12,288 read, HTTP 200, confident answer."""
    runtime, _ = build_runtime(
        Replies([conclude(["ev_big"])], input_tokens=120)
    )

    result = runtime.run(
        package=package,
        envelope=envelope,
        instruction="Test SCM-01.",
        evidence=[EvidenceItem("ev_big", "github", "merged without approval. " * 2000)],
    )

    assert result.status == "context_truncated"
    assert result.output is None
    assert "Raise the served context window" in result.summary


def test_the_token_floor_never_exceeds_a_pessimistic_estimate():
    """The two counters answer opposite questions and must not cross."""
    for text in ("", "a", "one two three", "x" * 500, "PR-1002 merged\n" * 50):
        assert token_floor(text) <= estimate_tokens(text)


def test_a_document_returned_inside_a_structure_is_still_screened(package, envelope):
    """Screening only bare strings left the hole exactly where the payload is largest."""
    secret = "AKIAIOSFODNN7EXAMPLE and password=hunter2hunter2"
    tool = BoundedTool("evidence.query", lambda **_: {"evidence": [{"content": secret}]})
    runtime, gateway = build_runtime(Replies([GATHER, conclude([])]), tools=[tool])

    result = runtime.run(package=package, envelope=envelope, instruction="Test SCM-01.")

    rendered = json.dumps(result.observations)
    assert "hunter2hunter2" not in rendered


def test_the_prompt_publishes_the_arguments_each_tool_takes(package, envelope):
    """Given only names, a live model invented them and every call was refused."""
    tool = BoundedTool(
        "tests.execute",
        lambda **_: {"population_count": 44},
        description='{"test_id": "SCM-01|IAM-01|SLA-01"} - REQUIRED',
    )
    client = Replies([conclude([])])
    runtime, _ = build_runtime(client, tools=[tool])

    runtime.run(package=package, envelope=envelope, instruction="Test SCM-01.")

    assert '"test_id": "SCM-01|IAM-01|SLA-01"' in client.prompts[0]


# -- domain tools --------------------------------------------------------------


def test_every_bound_tool_publishes_a_contract():
    """A tool whose contract the caller cannot read is not a usable tool."""
    context = DomainToolContext(database=None, repository_root=ROOT)
    for name, tool in build_domain_tools(context).items():
        assert tool.description, name
        assert "no published argument contract" not in tool.description, name


def test_registration_is_the_intersection_of_declared_and_implemented(package):
    """Binding a handler the package does not declare hides a policy refusal."""
    key = Ed25519PrivateKey.generate()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    gateway = AgentGateway(identity_verifier=AgentIdentityVerifier({"k": public_pem}))
    context = DomainToolContext(database=None, repository_root=ROOT)

    bound = register_domain_tools(gateway, package=package, context=context)

    declared = {item["name"] for item in package.tools["tools"]}
    assert set(bound) <= declared
    assert "tests.execute" in bound
    assert "request.create" in unimplemented_tools(package)
    assert set(bound).isdisjoint(unimplemented_tools(package))


def test_a_test_with_no_bound_population_is_refused(envelope):
    """The agent does not get to choose the population a signed test runs over."""
    context = DomainToolContext(
        database=None, repository_root=ROOT, control_tests=object()
    )
    handler = build_domain_tools(context)["tests.execute"].handler

    with pytest.raises(DomainToolError, match="no corpus population"):
        handler(arguments={"test_id": "MADE-UP-01"}, identity=None, envelope=envelope)


def test_reading_another_engagement_is_refused_not_narrowed(envelope):
    """Narrowing would answer a question the agent thinks it asked broadly."""
    context = DomainToolContext(database=None, repository_root=ROOT)
    handler = build_domain_tools(context)["evidence.query"].handler

    with pytest.raises(DomainToolError, match="outside the evidence scope"):
        handler(
            arguments={"engagement_id": "eng_someone_else"}, identity=None, envelope=envelope
        )
