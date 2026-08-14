"""Compose every demonstration into one tenant, so the product shows one audit.

Each demonstration in ``scripts/`` proves one component and, until now, owned its
own tenant and deleted it on the way in. That is right for a component proof and
wrong for the product: an evaluator who opens the cockpit sees a plan in one
tenant, a report in another, and a reasoning trace in a third, so no single
screen shows the lifecycle the platform claims to run.

This script runs the same demonstrations, in dependency order, against the one
tenant the product routes read. Nothing here fabricates state: every record is
written by the component that owns it, through the same service calls its own
demonstration makes.

    python scripts/seed_demo_tenant.py
    python scripts/seed_demo_tenant.py --model-mode local --base-url http://127.0.0.1:5000/v1

With ``--model-mode`` the two model-driven stages — the assurance loop and the
governed agent trace — call a real model instead of the scripted client. Every
other stage is deterministic either way.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from assuranceos.adjudication.demo import run_assurance_loop_demo  # noqa: E402
from assuranceos.agent_audit_demo import run_agent_audit_demo  # noqa: E402
from assuranceos.config import settings  # noqa: E402
from assuranceos.connectors.demo import run_connector_demo  # noqa: E402
from assuranceos.control_testing.demo import run_control_test_demo  # noqa: E402
from assuranceos.db import Database  # noqa: E402
from assuranceos.db.models import EngagementTask  # noqa: E402
from assuranceos.demo import TENANT_ID, run_golden_engagement  # noqa: E402
from assuranceos.vault import (  # noqa: E402
    BaselineContentInspector,
    Ed25519ManifestSigner,
    EvidenceVault,
    GoogleCloudStorageObjectStore,
)
from assuranceos.governance.demo import run_governance_demo  # noqa: E402
from assuranceos.governance.models_client import build_client  # noqa: E402
from assuranceos.governance.telemetry import TelemetryConfig, configure_telemetry  # noqa: E402
from assuranceos.ledger import AuditLedger  # noqa: E402
from assuranceos.onboarding_demo import run_onboarding_demo  # noqa: E402
from assuranceos.orchestration.demo import run_orchestrator_demo  # noqa: E402
from assuranceos.portfolio.demo import run_portfolio_demo  # noqa: E402
from assuranceos.reporting.demo import run_reporting_demo  # noqa: E402
from assuranceos.scheduling.demo import run_scheduler_demo  # noqa: E402
from assuranceos.service_delivery_demo import run_service_delivery_demo  # noqa: E402
from assuranceos.standards.demo import SCM_ENGAGEMENT, run_pack_compiler_demo  # noqa: E402
from assuranceos.vault.demo import run_evidence_vault_demo  # noqa: E402

WORKFLOW = ROOT / "examples/workflows/software-change-management.json"


def plan_task(database: Database, engagement_id: str, task_key: str) -> str | None:
    """The compiled plan's own step for a piece of work, if the plan has one.

    The governed stages run *inside* the compiled engagement rather than beside
    it, so the audit a viewer opens is one plan with agents working through it —
    not a plan that never started next to executions attributed to nothing. The
    ids the pack compiler mints are not knowable in advance, so they are looked
    up by task key at the point the stage runs. Returning ``None`` lets the
    demonstration fall back to the task it owns, which is what happens when it
    is run on its own.
    """
    with database.read_session() as session:
        task = session.scalars(
            select(EngagementTask)
            .where(EngagementTask.engagement_id == engagement_id)
            .where(EngagementTask.task_key == task_key)
        ).first()
    return task.task_id if task is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-mode",
        default="mock",
        choices=["mock", "local", "gemini", "vertex"],
        help="mock keeps every stage deterministic; local and gemini call a real model",
    )
    parser.add_argument("--model", default=None, help="model name override")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument(
        "--thinking",
        default="off",
        choices=["off", "on", "server-default"],
        help="reasoning-model deliberation; structured audit output needs it off",
    )
    parser.add_argument(
        "--tenant", default=TENANT_ID, help="the tenant every stage writes into"
    )
    args = parser.parse_args()

    configure_telemetry(TelemetryConfig(environment=settings.environment))

    client = None
    if args.model_mode != "mock":
        client = build_client(
            args.model_mode,
            model=args.model or (settings.gemini_model if "gem" in args.model_mode else None),
            base_url=args.base_url,
            enable_thinking={"off": False, "on": True, "server-default": None}[args.thinking],
        )

    database = Database(settings.database_url)
    ledger = AuditLedger(settings.database_url)
    signer = (
        Ed25519ManifestSigner.from_pem(
            settings.export_signing_private_key,
            key_id=settings.export_signing_key_id,
        )
        if settings.export_signing_private_key
        else None
    )
    vault = (
        EvidenceVault(
            database,
            GoogleCloudStorageObjectStore(settings.evidence_bucket or ""),
            export_signer=signer,
            inspector=BaselineContentInspector(),
        )
        if settings.evidence_storage == "gcs"
        else EvidenceVault.local(
            database,
            settings.evidence_root,
            export_signer=signer,
            inspector=BaselineContentInspector(),
        )
    )
    tenant = args.tenant

    # The golden audit goes first and is the only stage allowed to clear the
    # tenant: it establishes the organisation, the engagement, and the corpus
    # every later stage cites. Everything after it composes.
    stages: list[tuple[str, Callable[[], Any]]] = [
        (
            "golden audit",
            lambda: run_golden_engagement(ROOT / "demo/asteria", ledger, reset=True),
        ),
        (
            "public onboarding and profile approval",
            lambda: run_onboarding_demo(
                database=database, repository_root=ROOT, tenant_id=tenant, vault=vault
            ),
        ),
        (
            "risk portfolio and plan",
            lambda: run_portfolio_demo(database=database, tenant_id=tenant, reset=False),
        ),
        (
            "standards and Audit Pack compilation",
            lambda: run_pack_compiler_demo(
                database=database, repository_root=ROOT, tenant_id=tenant, reset=False
            ),
        ),
        (
            "deterministic control tests",
            lambda: run_control_test_demo(database, ROOT, tenant_id=tenant),
        ),
        (
            "durable orchestration",
            lambda: run_orchestrator_demo(
                database=database,
                demo_root=ROOT / "demo/asteria",
                workflow_path=WORKFLOW,
                tenant_id=tenant,
                reset=False,
            ),
        ),
        (
            "recurring schedule",
            lambda: run_scheduler_demo(
                database=database, workflow_path=WORKFLOW, tenant_id=tenant, reset=False
            ),
        ),
        (
            "read-only connectors",
            lambda: run_connector_demo(database, vault, tenant_id=tenant, reset=False),
        ),
        (
            "evidence vault and signed export",
            lambda: run_evidence_vault_demo(
                database=database,
                object_root=settings.evidence_root,
                demo_root=settings.demo_root,
                export_path=settings.evidence_export_root / "asteria-evidence-demo.zip",
                tenant_id=tenant,
                reset=False,
                vault=vault,
            ),
        ),
        (
            "service-delivery audit and its finding",
            lambda: run_service_delivery_demo(
                database=database, repository_root=ROOT, tenant_id=tenant
            ),
        ),
        (
            "assurance loop to verified closure",
            lambda: run_assurance_loop_demo(
                database=database,
                repository_root=ROOT,
                model_client=client,
                tenant_id=tenant,
                reset=False,
            ),
        ),
        (
            "evidence-grounded report",
            lambda: run_reporting_demo(database=database, tenant_id=tenant, reset=False),
        ),
        (
            "governed agent trace",
            lambda: run_governance_demo(
                database=database,
                repository_root=ROOT,
                model_client=client,
                tenant_id=tenant,
                engagement_id=SCM_ENGAGEMENT,
                task_id=plan_task(database, SCM_ENGAGEMENT, "capture-change-evidence"),
                reset=False,
            ),
        ),
        # Last, and the only stage where the model decides what it needs rather
        # than following a script. It is also the only stage that produces
        # metered token usage against a real endpoint, which is what the
        # engagement economics view has to read to say anything true.
        (
            "agent runs the audit",
            lambda: run_agent_audit_demo(
                database=database,
                repository_root=ROOT,
                model_client=client,
                tenant_id=tenant,
                engagement_id=SCM_ENGAGEMENT,
                task_id=plan_task(database, SCM_ENGAGEMENT, "execute-population-test"),
                vault=vault,
            ),
        ),
    ]

    summary: list[dict[str, Any]] = []
    try:
        for name, stage in stages:
            started = time.monotonic()
            result = stage()
            elapsed = time.monotonic() - started
            summary.append(
                {
                    "stage": name,
                    "seconds": round(elapsed, 2),
                    "result_keys": sorted(result)[:8] if isinstance(result, dict) else None,
                }
            )
            print(f"  {name:<42} {elapsed:6.2f}s", flush=True)
    finally:
        database.dispose()

    print(
        json.dumps(
            {
                "tenant_id": tenant,
                "model_mode": args.model_mode,
                "stages": summary,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
