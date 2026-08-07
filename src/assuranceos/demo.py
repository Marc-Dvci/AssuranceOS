from __future__ import annotations

from datetime import date
from pathlib import Path

from .db.models import Engagement, EvidenceRecord, Finding, Tenant
from .db.repositories import (
    AuditEventRepository,
    EngagementRepository,
    EvidenceRepository,
    FindingRepository,
    OutboxRepository,
    TenantRepository,
)
from .deterministic import run_scm_population_test
from .evidence import capture_file
from .ledger import AuditLedger
from .models import AuditEvent


TENANT_ID = "tnt_asteria_demo"
ENGAGEMENT_ID = "eng_asteria_scm_2026h2"


def run_golden_engagement(demo_root: Path, ledger: AuditLedger) -> dict:
    ledger.reset_tenant(TENANT_ID)
    evidence_paths = [
        demo_root / "sources/github/pull_requests.json",
        demo_root / "sources/jira/change_tickets.json",
        demo_root / "sources/governance/approved_exceptions.json",
        demo_root / "sources/confluence/change_management_policy.md",
    ]
    evidence = [capture_file(path, source_type=path.parts[-2]) for path in evidence_paths]
    test_result = run_scm_population_test(demo_root)

    prompt_attack = {
        "evidence_id": next(
            item.evidence_id
            for item in evidence
            if item.source_locator.endswith("change_management_policy.md")
        ),
        "classification": "prompt_injection",
        "action": "tainted_and_denied",
        "attempted_action": "expand scope and retrieve credentials",
        "canonical_state_mutated": False,
    }
    exceptions = test_result["exceptions"]
    finding_payload = {
        "finding_id": "fnd_scm_001",
        "status": "proposed",
        "title": "Production change merged without required approval",
        "criteria": (
            "SCM-01 requires an approved change ticket and at least one independent "
            "approval before merge."
        ),
        "affected_population": len(exceptions),
        "severity": "high",
        "human_gate": "finding_approval",
        "evidence_ids": [item.evidence_id for item in evidence],
    }

    events = [
        AuditEvent(
            event_type="schedule.occurrence.created",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload={"schedule_id": "sch_scm_semiannual", "nominal_due": "2026-08-01"},
        ),
        AuditEvent(
            event_type="engagement.started",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload={
                "audit_pack": "software-change-management@1.0.0",
                "period": ["2026-07-01", "2026-07-31"],
            },
        ),
        *[
            AuditEvent(
                event_type="evidence.captured",
                tenant_id=TENANT_ID,
                engagement_id=ENGAGEMENT_ID,
                payload=item.model_dump(mode="json"),
            )
            for item in evidence
        ],
        AuditEvent(
            event_type="test.completed",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload=test_result,
        ),
        AuditEvent(
            event_type="security.prompt_injection.denied",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload=prompt_attack,
        ),
        AuditEvent(
            event_type="finding.proposed",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload=finding_payload,
        ),
    ]

    # Canonical records, audit events, and the integration outbox commit together.
    with ledger.database.transaction() as session:
        TenantRepository(session).add(
            Tenant(
                tenant_id=TENANT_ID,
                slug="asteria-demo",
                name="Asteria Systems DemoCo",
                status="active",
                region="europe-west1",
            )
        )
        EngagementRepository(session).add(
            Engagement(
                engagement_id=ENGAGEMENT_ID,
                tenant_id=TENANT_ID,
                code="AST-SCM-2026-H2",
                title="Software Change Management Audit",
                status="fieldwork",
                audit_pack_ref="software-change-management@1.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                scope_json={"repositories": ["asteria/payments-api"]},
            )
        )
        evidence_repository = EvidenceRepository(session)
        for item in evidence:
            source_path = Path(item.source_locator)
            evidence_repository.add(
                EvidenceRecord(
                    evidence_id=item.evidence_id,
                    tenant_id=TENANT_ID,
                    engagement_id=ENGAGEMENT_ID,
                    source_type=item.source_type,
                    source_locator=item.source_locator,
                    content_sha256=item.sha256,
                    object_uri=source_path.resolve().as_uri(),
                    mime_type=(
                        "application/json" if source_path.suffix == ".json" else "text/markdown"
                    ),
                    size_bytes=source_path.stat().st_size,
                    classification=item.classification,
                    collected_at=item.collected_at,
                    accepted=item.accepted,
                    tainted=item.evidence_id == prompt_attack["evidence_id"],
                    metadata_json={"synthetic": True},
                )
            )
        FindingRepository(session).add(
            Finding(
                finding_id=finding_payload["finding_id"],
                tenant_id=TENANT_ID,
                engagement_id=ENGAGEMENT_ID,
                code="SCM-001",
                version=1,
                title=finding_payload["title"],
                status="proposed",
                severity="high",
                confidence=0.95,
                risk_statement="Unauthorized or unreviewed production changes may be deployed.",
                criteria=finding_payload["criteria"],
                observed_condition=(
                    "One in-period production change lacked the required independent approval."
                ),
                affected_population_json={
                    "population": test_result["population_count"],
                    "exceptions": test_result["exception_count"],
                },
            )
        )
        AuditEventRepository(session).append_many(events)
        OutboxRepository(session).add(
            tenant_id=TENANT_ID,
            aggregate_type="finding",
            aggregate_id=finding_payload["finding_id"],
            event_type="finding.proposed",
            payload=finding_payload,
            idempotency_key=f"finding.proposed:{finding_payload['finding_id']}:v1",
        )

    return {
        "tenant_id": TENANT_ID,
        "engagement_id": ENGAGEMENT_ID,
        "test_result": test_result,
        "finding": finding_payload,
        "security_event": prompt_attack,
        "event_count": len(events),
    }
