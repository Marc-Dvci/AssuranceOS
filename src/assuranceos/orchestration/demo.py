from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from assuranceos.db import Database
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.deterministic import run_scm_population_test

from .definitions import EngagementStatus, GateDecision, TaskExecutionResult, WorkflowDefinition
from .replay import verify_replay
from .service import Orchestrator
from .worker import LocalWorker

ORCHESTRATION_DEMO_TENANT_ID = "tnt_asteria_orchestration"
ORCHESTRATION_DEMO_ENGAGEMENT_ID = "eng_asteria_scm_orchestrated"


def load_workflow(path: Path) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate_json(path.read_text(encoding="utf-8"))


def run_orchestrator_demo(
    *,
    database: Database,
    demo_root: Path,
    workflow_path: Path,
    tenant_id: str | None = None,
    reset: bool = True,
) -> dict[str, Any]:
    """Run the SCM vertical slice through the durable orchestration contract.

    ``tenant_id`` retargets the demonstration so several demonstrations can
    compose one complete tenant; ``reset`` keeps whatever that tenant already
    holds instead of deleting it first.
    """
    tenant = tenant_id or ORCHESTRATION_DEMO_TENANT_ID
    _reset_and_seed(database, tenant, reset=reset)
    orchestrator = Orchestrator(database)
    workflow = load_workflow(workflow_path)
    orchestrator.compile_workflow(
        tenant_id=tenant,
        engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID,
        workflow=workflow,
    )
    orchestrator.start_engagement(
        tenant_id=tenant,
        engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID,
    )

    test_result: dict[str, Any] = {}

    def collect_evidence(_lease) -> TaskExecutionResult:
        paths = [
            demo_root / "sources/github/pull_requests.json",
            demo_root / "sources/jira/change_tickets.json",
            demo_root / "sources/governance/approved_exceptions.json",
            demo_root / "sources/confluence/change_management_policy.md",
        ]
        return TaskExecutionResult(
            output_refs=[path.resolve().as_uri() for path in paths],
            result={"source_count": len(paths), "synthetic": True},
        )

    def execute_test(_lease) -> TaskExecutionResult:
        nonlocal test_result
        test_result = run_scm_population_test(demo_root)
        return TaskExecutionResult(
            output_refs=["test-result:scm_population_test@1.0.0"],
            result=test_result,
        )

    def skeptic_review(_lease) -> TaskExecutionResult:
        return TaskExecutionResult(
            result={
                "supported_exception_count": test_result.get("exception_count", 0),
                "rejected_false_positives": test_result.get("rejected_false_positives", []),
                "conclusion": (
                    "supported" if test_result.get("exception_count") else "not_supported"
                ),
            }
        )

    def propose_finding(_lease) -> TaskExecutionResult:
        return TaskExecutionResult(
            output_refs=["finding:fnd_scm_001"],
            result={
                "finding_id": "fnd_scm_001",
                "status": "proposed",
                "severity": "high",
                "affected_population": test_result.get("exception_count", 0),
            },
        )

    def issue_report(_lease) -> TaskExecutionResult:
        return TaskExecutionResult(
            output_refs=["report:scm-engagement-report-v1"],
            result={"status": "issued", "material_claims_evidence_linked": True},
        )

    worker = LocalWorker(
        orchestrator=orchestrator,
        tenant_id=tenant,
        worker_id="local-demo-worker",
        handlers={
            "evidence_collection": collect_evidence,
            "deterministic_test": execute_test,
            "skeptic_review": skeptic_review,
            "finding_adjudication": propose_finding,
            "report_generation": issue_report,
        },
    )

    worker_runs: list[dict[str, Any]] = []
    approvals: list[dict[str, str]] = []
    for _ in range(50):
        snapshot = orchestrator.snapshot(
            tenant_id=tenant,
            engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID,
        )
        if snapshot.status == EngagementStatus.COMPLETED:
            break
        waiting = [task for task in snapshot.tasks if task.status == "waiting_approval"]
        if waiting:
            for task in waiting:
                reason = f"Synthetic demo approval for {task.human_gate}."
                orchestrator.approve_gate(
                    tenant_id=tenant,
                    task_id=task.task_id,
                    decision=GateDecision(actor_id="usr_demo_reviewer", reason=reason),
                )
                approvals.append({"task_key": task.task_key, "gate": task.human_gate or ""})
            continue
        run = worker.run_once(engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID)
        if run is None:
            raise RuntimeError("orchestrator demo stalled without runnable work or a human gate")
        worker_runs.append(
            {
                "task_id": run.task_id,
                "task_key": run.task_key,
                "outcome": run.outcome,
                "attempt_count": run.attempt_count,
            }
        )
    else:
        raise RuntimeError("orchestrator demo exceeded the execution safety limit")

    final = orchestrator.snapshot(
        tenant_id=tenant,
        engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID,
    )
    replay = verify_replay(
        database,
        orchestrator,
        tenant_id=tenant,
        engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID,
    )
    return {
        "tenant_id": tenant,
        "engagement_id": ORCHESTRATION_DEMO_ENGAGEMENT_ID,
        "engagement_status": final.status,
        "task_states": {task.task_key: task.status for task in final.tasks},
        "worker_runs": worker_runs,
        "approvals": approvals,
        "test_result": test_result,
        "replay_matches_canonical": replay.matches,
    }


def _reset_and_seed(database: Database, tenant: str, *, reset: bool = True) -> None:
    if reset:
        with database.transaction() as session:
            existing = TenantRepository(session).get(tenant)
            if existing is not None:
                session.delete(existing)
    with database.transaction() as session:
        repository = TenantRepository(session)
        if repository.get(tenant) is None:
            repository.add(
                Tenant(
                    tenant_id=tenant,
                    slug="asteria-orchestration-demo",
                    name="Asteria Systems DemoCo — Orchestration",
                    status="active",
                    region="europe-west1",
                )
            )
            session.flush()
        # Composing onto a tenant another demonstration populated must not
        # duplicate the records this one owns.
        if session.get(Engagement, ORCHESTRATION_DEMO_ENGAGEMENT_ID) is not None:
            return
        session.add(
            Engagement(
                engagement_id=ORCHESTRATION_DEMO_ENGAGEMENT_ID,
                tenant_id=tenant,
                code="AST-SCM-ORCH-2026-H2",
                title="Software Change Management Audit — Orchestrated",
                status="planned",
                audit_pack_ref="software-change-management@1.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                scope_json={"repositories": ["asteria/payments-api"], "synthetic": True},
            )
        )
