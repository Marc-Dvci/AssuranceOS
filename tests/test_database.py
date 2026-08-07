from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from assuranceos.db.models import (
    ApprovalDecision,
    AuditPlan,
    AuditSchedule,
    Engagement,
    EngagementTask,
    EngagementTemplate,
    Finding,
    ManagementResponse,
    OrganizationFact,
    OrganizationProfile,
    OutboxEvent,
    RemediationAction,
    Retest,
    ScheduleOccurrence,
    Tenant,
)
from assuranceos.db.repositories import (
    DuplicateRecordError,
    EngagementRepository,
    FindingRepository,
    IdempotencyRepository,
    OrganizationRepository,
    OutboxRepository,
    PlanningRepository,
    RemediationRepository,
    TenantRepository,
)
from assuranceos.db.session import Database


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "canonical.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


def test_schema_contains_canonical_domain_tables(database):
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "tenants",
        "organization_profiles",
        "organization_facts",
        "audit_universe_entities",
        "risks",
        "controls",
        "audit_plans",
        "audit_schedules",
        "schedule_occurrences",
        "engagements",
        "engagement_tasks",
        "evidence_records",
        "evidence_custody_events",
        "claims",
        "findings",
        "approval_decisions",
        "remediation_actions",
        "retests",
        "agent_releases",
        "execution_traces",
        "audit_events",
        "outbox_events",
        "idempotency_records",
    }.issubset(tables)


def test_organization_profiles_are_versioned_and_tenant_scoped(database):
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        TenantRepository(session).add(Tenant(tenant_id="tnt_b", slug="b", name="Tenant B"))
        organizations = OrganizationRepository(session)
        organizations.add_profile(
            OrganizationProfile(
                profile_id="org_a_v1",
                tenant_id="tnt_a",
                version=1,
                legal_name="Asteria Systems DemoCo",
            )
        )
        organizations.add_profile(
            OrganizationProfile(
                profile_id="org_a_v2",
                tenant_id="tnt_a",
                version=2,
                legal_name="Asteria Systems DemoCo",
                status="canonical",
            )
        )
        organizations.add_fact(
            OrganizationFact(
                fact_id="fact_industry",
                tenant_id="tnt_a",
                profile_id="org_a_v2",
                fact_key="industry",
                value_json="software",
                claim_type="observed_fact",
                status="accepted",
            )
        )

    with database.read_session() as session:
        organizations = OrganizationRepository(session)
        assert organizations.latest_profile("tnt_a").profile_id == "org_a_v2"
        assert organizations.get_profile("tnt_b", "org_a_v2") is None
        assert organizations.list_facts("tnt_a", "org_a_v2")[0].value_json == "software"


def test_domain_write_and_outbox_commit_atomically(database):
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        engagement = Engagement(
            engagement_id="eng_1",
            tenant_id="tnt_a",
            code="SCM-2026",
            title="SCM Audit",
            audit_pack_ref="scm@1",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )
        EngagementRepository(session).add(engagement)
        OutboxRepository(session).add(
            tenant_id="tnt_a",
            aggregate_type="engagement",
            aggregate_id=engagement.engagement_id,
            event_type="engagement.created",
            payload={"engagement_id": engagement.engagement_id},
            idempotency_key="engagement.created:eng_1",
        )

    with database.read_session() as session:
        assert EngagementRepository(session).get("tnt_a", "eng_1") is not None
        outbox = list(session.scalars(select(OutboxEvent)))
        assert len(outbox) == 1
        assert outbox[0].event_type == "engagement.created"


def test_transaction_rolls_back_domain_and_outbox_together(database):
    with pytest.raises(RuntimeError):
        with database.transaction() as session:
            TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
            OutboxRepository(session).add(
                tenant_id="tnt_a",
                aggregate_type="tenant",
                aggregate_id="tnt_a",
                event_type="tenant.created",
                payload={},
                idempotency_key="tenant.created:tnt_a",
            )
            raise RuntimeError("simulated failure")

    with database.read_session() as session:
        assert TenantRepository(session).get("tnt_a") is None
        assert list(session.scalars(select(OutboxEvent))) == []


def test_task_transition_is_compare_and_set(database):
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        engagements = EngagementRepository(session)
        engagements.add(
            Engagement(
                engagement_id="eng_1",
                tenant_id="tnt_a",
                code="SCM-2026",
                title="SCM Audit",
                audit_pack_ref="scm@1",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
        engagements.add_task(
            EngagementTask(
                task_id="tsk_1",
                tenant_id="tnt_a",
                engagement_id="eng_1",
                task_key="collect-github",
                task_type="evidence_collection",
                definition_version="1",
                idempotency_key="eng_1:collect-github:v1",
            )
        )
        assert engagements.transition_task(
            "tnt_a", "tsk_1", expected_status="pending", new_status="ready"
        )
        assert not engagements.transition_task(
            "tnt_a", "tsk_1", expected_status="pending", new_status="ready"
        )


def test_idempotency_key_is_unique_per_tenant(database):
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        IdempotencyRepository(session).begin(
            tenant_id="tnt_a",
            idempotency_key="request-1",
            operation="engagement.create",
            request_fingerprint="a" * 64,
        )

    with pytest.raises(DuplicateRecordError):
        with database.transaction() as session:
            IdempotencyRepository(session).begin(
                tenant_id="tnt_a",
                idempotency_key="request-1",
                operation="engagement.create",
                request_fingerprint="a" * 64,
            )


def test_outbox_mark_published_is_idempotent(database):
    now = datetime.now(timezone.utc)
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        event = OutboxRepository(session).add(
            tenant_id="tnt_a",
            aggregate_type="tenant",
            aggregate_id="tnt_a",
            event_type="tenant.created",
            payload={},
            idempotency_key="tenant.created:tnt_a",
        )
        outbox_id = event.outbox_id

    with database.transaction() as session:
        outbox = OutboxRepository(session)
        claimed = outbox.claim(worker_id="publisher-a", now=now + timedelta(seconds=1), lease_seconds=30, limit=1)
        assert [item.outbox_id for item in claimed] == [outbox_id]
        assert outbox.mark_published(
            outbox_id, worker_id="publisher-a", published_at=now, message_id="msg-1"
        )
        assert not outbox.mark_published(
            outbox_id, worker_id="publisher-a", published_at=now, message_id="msg-1"
        )


def test_planning_repository_preserves_schedule_versions_and_occurrences(database):
    nominal_due = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        planning = PlanningRepository(session)
        planning.add_plan(
            AuditPlan(
                plan_id="plan_1",
                tenant_id="tnt_a",
                name="2026 rolling plan",
                version=1,
                status="approved",
            )
        )
        planning.add_template(
            EngagementTemplate(
                template_id="tpl_1",
                tenant_id="tnt_a",
                name="SCM audit",
                version=1,
                status="released",
                audit_pack_ref="scm@1",
            )
        )
        planning.add_schedule(
            AuditSchedule(
                schedule_id="sch_1",
                tenant_id="tnt_a",
                plan_id="plan_1",
                template_id="tpl_1",
                name="SCM semiannual",
                version=1,
                status="active",
                recurrence_rule="FREQ=MONTHLY;INTERVAL=6",
                timezone="Europe/Paris",
            )
        )
        planning.add_occurrence(
            ScheduleOccurrence(
                occurrence_id="occ_1",
                tenant_id="tnt_a",
                schedule_id="sch_1",
                nominal_due=nominal_due,
                eligible_at=nominal_due,
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                schedule_version=1,
                template_version=1,
            )
        )

    with database.read_session() as session:
        planning = PlanningRepository(session)
        assert planning.get_schedule("tnt_a", "sch_1").version == 1
        assert [item.occurrence_id for item in planning.list_occurrences("tnt_a", "sch_1")] == [
            "occ_1"
        ]


def test_finding_decision_remediation_and_retest_are_persisted(database):
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        EngagementRepository(session).add(
            Engagement(
                engagement_id="eng_1",
                tenant_id="tnt_a",
                code="SCM-2026",
                title="SCM Audit",
                audit_pack_ref="scm@1",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
            )
        )
        findings = FindingRepository(session)
        findings.add(
            Finding(
                finding_id="fnd_1",
                tenant_id="tnt_a",
                engagement_id="eng_1",
                code="SCM-001",
                title="Missing approval",
                severity="high",
                confidence=0.95,
                risk_statement="Unreviewed changes may be deployed.",
                criteria="Independent approval is required.",
                observed_condition="One change lacked approval.",
            )
        )
        findings.add_decision(
            ApprovalDecision(
                decision_id="dec_1",
                tenant_id="tnt_a",
                engagement_id="eng_1",
                finding_id="fnd_1",
                decision_type="approve",
                actor_id="usr_reviewer",
                reason="Evidence is sufficient and the exception is material.",
            )
        )
        remediation = RemediationRepository(session)
        remediation.add_management_response(
            ManagementResponse(
                response_id="rsp_1",
                tenant_id="tnt_a",
                finding_id="fnd_1",
                version=1,
                response_text="Management agrees.",
                action_plan="Enforce protected branch rules.",
                submitted_by="usr_owner",
            )
        )
        remediation.add_action(
            RemediationAction(
                action_id="act_1",
                tenant_id="tnt_a",
                finding_id="fnd_1",
                owner_ref="usr_owner",
                due_date=date(2026, 9, 30),
                action_plan="Enforce protected branch rules.",
            )
        )
        remediation.add_retest(
            Retest(
                retest_id="rts_1",
                tenant_id="tnt_a",
                action_id="act_1",
                engagement_id="eng_1",
                procedure_ref="SCM-01-retest@1",
            )
        )

    with database.read_session() as session:
        assert FindingRepository(session).list_decisions("tnt_a", "fnd_1")[0].decision_type == (
            "approve"
        )
        actions = RemediationRepository(session).list_actions_for_finding("tnt_a", "fnd_1")
        assert [item.action_id for item in actions] == ["act_1"]


def test_database_create_schema_has_no_external_import_order_dependency(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "isolated-schema.db"
    script = (
        "from assuranceos.db import Database; "
        "from sqlalchemy import inspect; "
        f"db=Database.from_sqlite_path(__import__('pathlib').Path({str(database_path)!r})); "
        "db.create_schema(); "
        "tables=set(inspect(db.engine).get_table_names()); "
        "assert 'tenants' in tables and 'evidence_custody_events' in tables; "
        "db.dispose()"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env={**__import__("os").environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
