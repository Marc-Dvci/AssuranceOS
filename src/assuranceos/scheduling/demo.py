from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from assuranceos.db import Database
from assuranceos.db.models import AuditPlan, AuditSchedule, EngagementTemplate, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.orchestration import Orchestrator, WorkflowDefinition

from .definitions import PreflightContext
from .service import AuditScheduler

SCHEDULER_DEMO_TENANT_ID = "tnt_asteria_scheduler_demo"
SCHEDULER_DEMO_SCHEDULE_ID = "sch_asteria_scm_semiannual"
SCHEDULER_DEMO_NOW = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)


def run_scheduler_demo(*, database: Database, workflow_path: Path) -> dict[str, Any]:
    workflow = WorkflowDefinition.model_validate(
        json.loads(workflow_path.read_text(encoding="utf-8"))
    )
    _reset_and_seed(database, workflow)

    def clock() -> datetime:
        return SCHEDULER_DEMO_NOW

    orchestrator = Orchestrator(database, clock=clock)
    scheduler = AuditScheduler(database, clock=clock, orchestrator=orchestrator)

    simulation = scheduler.simulate(
        tenant_id=SCHEDULER_DEMO_TENANT_ID,
        schedule_id=SCHEDULER_DEMO_SCHEDULE_ID,
        window_start=SCHEDULER_DEMO_NOW - timedelta(days=1),
        window_end=SCHEDULER_DEMO_NOW + timedelta(days=550),
    )
    summary = scheduler.evaluate_due(
        tenant_id=SCHEDULER_DEMO_TENANT_ID,
        schedule_id=SCHEDULER_DEMO_SCHEDULE_ID,
        context=PreflightContext(
            connector_health={"github": "healthy", "jira": "healthy"},
            available_budget_usd=25,
            available_competencies={"internal_auditor", "technology_auditor"},
        ),
    )
    occurrence = scheduler.list_occurrences(
        tenant_id=SCHEDULER_DEMO_TENANT_ID,
        schedule_id=SCHEDULER_DEMO_SCHEDULE_ID,
    )[0]
    orchestration = orchestrator.snapshot(
        tenant_id=SCHEDULER_DEMO_TENANT_ID,
        engagement_id=occurrence.engagement_id or "",
    )
    return {
        "tenant_id": SCHEDULER_DEMO_TENANT_ID,
        "schedule_id": SCHEDULER_DEMO_SCHEDULE_ID,
        "evaluation": summary.model_dump(mode="json"),
        "occurrence": occurrence.model_dump(mode="json"),
        "engagement_status": orchestration.status,
        "ready_tasks": [
            task.task_key for task in orchestration.tasks if task.status == "ready"
        ],
        "future_occurrences": [item.model_dump(mode="json") for item in simulation],
    }


def _reset_and_seed(database: Database, workflow: WorkflowDefinition) -> None:
    with database.transaction() as session:
        tenant = TenantRepository(session).get(SCHEDULER_DEMO_TENANT_ID)
        if tenant is not None:
            session.delete(tenant)
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(
                tenant_id=SCHEDULER_DEMO_TENANT_ID,
                slug="asteria-scheduler-demo",
                name="Asteria Systems DemoCo — Scheduler",
                status="active",
                region="europe-west1",
            )
        )
        session.add(
            AuditPlan(
                plan_id="plan_asteria_2026",
                tenant_id=SCHEDULER_DEMO_TENANT_ID,
                name="Asteria 2026 rolling audit plan",
                version=1,
                status="approved",
                approved_at=SCHEDULER_DEMO_NOW - timedelta(days=30),
                approved_by="usr_audit_sponsor",
            )
        )
        session.add(
            EngagementTemplate(
                template_id="tpl_asteria_scm",
                tenant_id=SCHEDULER_DEMO_TENANT_ID,
                name="Software Change Management Audit",
                version=1,
                status="released",
                audit_pack_ref="software-change-management@1.0.0",
                objectives_json=["Assess whether production changes are authorized and traceable."],
                scope_json={"repositories": ["asteria/payments-api"], "synthetic": True},
                workflow_definition_json=workflow.model_dump(mode="json"),
            )
        )
        session.flush()
        session.add(
            AuditSchedule(
                schedule_id=SCHEDULER_DEMO_SCHEDULE_ID,
                tenant_id=SCHEDULER_DEMO_TENANT_ID,
                plan_id="plan_asteria_2026",
                template_id="tpl_asteria_scm",
                name="Asteria SCM semiannual schedule",
                version=1,
                status="active",
                recurrence_rule="FREQ=MONTHLY;INTERVAL=6;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
                timezone="Europe/Paris",
                effective_from=datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc),
                audit_period_rule_json={"kind": "calendar_months", "months": 6},
                business_calendar_json={
                    "weekend_days": [5, 6],
                    "holidays": ["2027-01-01"],
                },
                preflight_policy_json={
                    "required_connectors": ["github", "jira"],
                    "required_competencies": ["technology_auditor"],
                    "estimated_cost_usd": 8,
                    "independence_roles": ["engagement_owner"],
                },
                launch_mode="automatic",
                missed_occurrence_policy="launch_latest",
                overlap_policy="prevent",
                max_concurrent_engagements=1,
            )
        )
