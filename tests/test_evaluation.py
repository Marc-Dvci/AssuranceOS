from __future__ import annotations

import json
from pathlib import Path
import shutil

import yaml

from assuranceos.evaluation import AgentEvaluationRunner
from assuranceos.governance.models_client import ScriptedClient
from assuranceos.registry import AgentRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_contract_qualification_executes_all_published_fleet_cases():
    report = AgentEvaluationRunner(repository_root=ROOT).run()
    summary = report.as_dict(include_agents=False)

    assert report.passed is True
    assert summary["agent_count"] == summary["passed_agents"] == 19
    assert summary["case_count"] == summary["passed_cases"] == 76
    assert summary["suites"] == {
        "golden": {"cases": 19, "passed": 19},
        "missing_evidence": {"cases": 19, "passed": 19},
        "adversarial": {"cases": 19, "passed": 19},
        "cross_industry": {"cases": 19, "passed": 19},
    }
    assert all(agent.metrics["schema_validity"] == 1.0 for agent in report.agents)


def test_qualification_fails_when_a_privileged_attack_has_a_route(tmp_path):
    agents = tmp_path / "agents"
    package = agents / "company-intelligence"
    shutil.copytree(ROOT / "agents" / "company-intelligence", package)
    policy_path = package / "policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["forbidden_actions"].remove("credentials.read")
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    report = AgentEvaluationRunner(
        repository_root=ROOT,
        registry=AgentRegistry(agents, verify_releases=False),
    ).run()

    assert report.passed is False
    adversarial = next(case for case in report.cases if case.suite == "adversarial")
    assert any(
        check.name == "prohibited_tool_calls" and not check.passed
        for check in adversarial.checks
    )


def _output(conclusion: str, *, evidence: list[str], missing: list[str] | None = None) -> str:
    return json.dumps(
        {
            "task_id": "eval-task",
            "agent_role": "company-intelligence",
            "conclusion": conclusion,
            "summary": "Scoped release qualification result.",
            "claim_type": "observed_fact" if evidence else "unknown",
            "evidence_references": [{"evidence_id": item} for item in evidence],
            "missing_evidence": missing or [],
            "contradictory_evidence": [],
            "assumptions": [],
            "confidence": 0.9 if evidence else 0.0,
            "recommended_next_action": "Continue under the released workflow.",
            "policy_checks": {"scope": True, "evidence": bool(evidence)},
            "requires_human_approval": True,
        }
    )


def test_model_mode_scores_structured_outputs_in_addition_to_contracts():
    client = ScriptedClient(
        replies=[
            _output("effective", evidence=["ev_eval_accepted"]),
            _output("insufficient_evidence", evidence=[], missing=["required source"]),
            _output("blocked", evidence=["ev_eval_adversarial"]),
            _output("insufficient_evidence", evidence=[], missing=["industry evidence"]),
        ],
        model_name="qualification-fixture",
    )
    report = AgentEvaluationRunner(repository_root=ROOT, model_client=client).run(
        agent_ids=["company-intelligence"]
    )

    assert report.mode == "model"
    assert report.model == "qualification-fixture"
    assert report.passed is True
    assert len(client.calls) == 4
    assert all(case.model == "qualification-fixture" for case in report.cases)
