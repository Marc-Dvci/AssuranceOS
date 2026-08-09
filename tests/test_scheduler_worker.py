from __future__ import annotations

from datetime import datetime, timezone

from assuranceos.db import Database
from assuranceos.db.models import AuditPlan, AuditSchedule, EngagementTemplate, Tenant
from assuranceos.db.repositories import TenantRepository
from scripts.run_scheduler_worker import active_tenants


def test_scheduler_worker_discovers_only_active_tenants(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "scheduler-worker.db")
    database.create_schema()
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    try:
        with database.transaction() as session:
            for tenant_id in ("tnt_b", "tnt_a", "tnt_c"):
                TenantRepository(session).add(
                    Tenant(tenant_id=tenant_id, slug=tenant_id, name=tenant_id)
                )
                session.add(
                    AuditPlan(
                        plan_id=f"plan_{tenant_id}",
                        tenant_id=tenant_id,
                        name="Plan",
                        version=1,
                    )
                )
                session.add(
                    EngagementTemplate(
                        template_id=f"template_{tenant_id}",
                        tenant_id=tenant_id,
                        name="Template",
                        version=1,
                        audit_pack_ref="test@1",
                        workflow_definition_json={"workflow_version": "1", "tasks": []},
                    )
                )
        with database.transaction() as session:
            for tenant_id, status in (("tnt_b", "active"), ("tnt_a", "active"), ("tnt_c", "draft")):
                session.add(
                    AuditSchedule(
                        schedule_id=f"sch_{tenant_id}",
                        tenant_id=tenant_id,
                        name="Schedule",
                        version=1,
                        status=status,
                        plan_id=f"plan_{tenant_id}",
                        template_id=f"template_{tenant_id}",
                        recurrence_rule="FREQ=DAILY",
                        timezone="UTC",
                        effective_from=now,
                    )
                )
        assert active_tenants(database) == ["tnt_a", "tnt_b"]
    finally:
        database.dispose()
