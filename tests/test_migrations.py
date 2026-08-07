from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from conftest import alembic_head as current_head


def test_initial_migration_upgrades_fresh_database(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ASSURANCEOS_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "alembic_version" in tables
        assert "tenants" in tables
        assert "engagements" in tables
        assert "outbox_events" in tables
        assert "schedule_cursors" in tables
        assert "evidence_custody_events" in tables
        assert "connector_instances" in tables
        assert "collection_grants" in tables
        assert "connector_runs" in tables
        assert "control_test_releases" in tables
        assert "control_test_runs" in tables
        assert "control_test_dataset_bindings" in tables
        assert "control_test_exceptions" in tables
        assert "agent_identities" in tables
        assert "agent_gateway_decisions" in tables
        assert "agent_guardrail_findings" in tables
        assert "agent_reasoning_spans" in tables
        with engine.connect() as connection:
            versions = connection.execute(
                text("select version_num from alembic_version")
            ).scalars().all()
            assert versions == [current_head()]
    finally:
        engine.dispose()


def test_orchestration_migration_backfills_existing_event_stream_order(tmp_path, monkeypatch):
    database_path = tmp_path / "upgrade.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ASSURANCEOS_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "fa73e07500b5")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(tenant_id, slug, name, status, region, created_at, updated_at) "
                    "VALUES ('tnt_a', 'a', 'Tenant A', 'active', NULL, "
                    "'2026-08-06 12:00:00', '2026-08-06 12:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO audit_events "
                    "(event_id, event_type, tenant_id, engagement_id, task_id, occurred_at, payload_json) "
                    "VALUES ('evt_1', 'tenant.seeded', 'tnt_a', NULL, NULL, "
                    "'2026-08-06 12:00:00', '{}')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT stream_id, sequence_no FROM audit_events WHERE event_id='evt_1'"
                )
            ).one()
            assert row.stream_id == "tenant:tnt_a"
            assert row.sequence_no == 1
        task_columns = {column["name"] for column in inspect(engine).get_columns("engagement_tasks")}
        assert {"priority", "available_at", "deadline_at", "result_json", "last_error"}.issubset(
            task_columns
        )
    finally:
        engine.dispose()


def test_scheduler_migration_backfills_existing_schedule_and_occurrence(tmp_path, monkeypatch):
    database_path = tmp_path / "scheduler-upgrade.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ASSURANCEOS_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "0002_durable_orchestration")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(tenant_id, slug, name, status, region, created_at, updated_at) "
                    "VALUES ('tnt_a', 'a', 'Tenant A', 'active', NULL, "
                    "'2026-07-01 08:00:00', '2026-07-01 08:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO audit_plans "
                    "(plan_id, tenant_id, name, version, status, horizon_start, horizon_end, "
                    "coverage_policy_json, approved_at, approved_by, created_at, updated_at) "
                    "VALUES ('plan_1', 'tnt_a', 'Plan', 1, 'approved', NULL, NULL, '{}', "
                    "NULL, NULL, '2026-07-01 08:00:00', '2026-07-01 08:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO engagement_templates "
                    "(template_id, tenant_id, name, version, status, audit_pack_ref, "
                    "objectives_json, scope_json, preflight_policy_json, created_at, updated_at) "
                    "VALUES ('tpl_1', 'tnt_a', 'SCM', 1, 'released', 'scm@1', '[]', '{}', '{}', "
                    "'2026-07-01 08:00:00', '2026-07-01 08:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO audit_schedules "
                    "(schedule_id, tenant_id, plan_id, template_id, name, version, status, "
                    "recurrence_rule, timezone, audit_period_rule_json, blackout_policy_json, "
                    "preflight_policy_json, launch_mode, created_at, updated_at) "
                    "VALUES ('sch_1', 'tnt_a', 'plan_1', 'tpl_1', 'Semiannual', 1, 'active', "
                    "'FREQ=MONTHLY;INTERVAL=6', 'UTC', '{}', '{}', '{}', 'automatic', "
                    "'2026-07-01 08:00:00', '2026-07-01 08:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_occurrences "
                    "(occurrence_id, tenant_id, schedule_id, engagement_id, nominal_due, status, "
                    "decision_reason, schedule_version, created_at) "
                    "VALUES ('occ_1', 'tnt_a', 'sch_1', NULL, '2026-08-01 08:00:00', 'due', "
                    "NULL, 1, '2026-08-01 08:00:00')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "schedule_cursors" in inspector.get_table_names()
        schedule_columns = {column["name"] for column in inspector.get_columns("audit_schedules")}
        assert {
            "effective_from",
            "business_calendar_json",
            "missed_occurrence_policy",
            "catch_up_limit",
            "overlap_policy",
            "max_concurrent_engagements",
        }.issubset(schedule_columns)
        with engine.connect() as connection:
            schedule = connection.execute(
                text(
                    "SELECT effective_from, missed_occurrence_policy, catch_up_limit "
                    "FROM audit_schedules WHERE schedule_id='sch_1'"
                )
            ).one()
            occurrence = connection.execute(
                text(
                    "SELECT eligible_at, period_start, period_end, template_version "
                    "FROM schedule_occurrences WHERE occurrence_id='occ_1'"
                )
            ).one()
            workflow = connection.execute(
                text(
                    "SELECT workflow_definition_json FROM engagement_templates "
                    "WHERE template_id='tpl_1'"
                )
            ).scalar_one()
        assert str(schedule.effective_from).startswith("2026-07-01 08:00:00")
        assert schedule.missed_occurrence_policy == "launch_latest"
        assert schedule.catch_up_limit == 12
        assert str(occurrence.eligible_at).startswith("2026-08-01 08:00:00")
        assert str(occurrence.period_start) == "2026-08-01"
        assert str(occurrence.period_end) == "2026-08-01"
        assert occurrence.template_version == 1
        assert workflow in ({}, "{}")
    finally:
        engine.dispose()


def test_evidence_vault_migration_backfills_legacy_custody_genesis(tmp_path, monkeypatch):
    from assuranceos.db.session import Database
    from assuranceos.vault import EvidenceVault

    database_path = tmp_path / "evidence-upgrade.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ASSURANCEOS_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "0003_recurring_scheduler")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(tenant_id, slug, name, status, region, created_at, updated_at) "
                    "VALUES ('tnt_a', 'a', 'Tenant A', 'active', NULL, "
                    "'2026-08-06 12:00:00', '2026-08-06 12:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO evidence_records ("
                    "evidence_id, tenant_id, engagement_id, task_id, source_type, source_locator, "
                    "content_sha256, object_uri, mime_type, size_bytes, classification, source_time, "
                    "collected_at, accepted, tainted, retention_until, legal_hold, metadata_json"
                    ") VALUES ("
                    "'evd_legacy', 'tnt_a', NULL, NULL, 'upload', 'upload://legacy', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'file:///legacy/evidence.json', 'application/json', 2, 'internal', NULL, "
                    "'2026-08-06 12:30:00', 1, 0, NULL, 0, '{}')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            record = connection.execute(
                text(
                    "SELECT record_kind, storage_provider, integrity_status, storage_key "
                    "FROM evidence_records WHERE evidence_id='evd_legacy'"
                )
            ).one()
            custody = connection.execute(
                text(
                    "SELECT action, sequence_no, previous_event_hash, event_hash "
                    "FROM evidence_custody_events WHERE evidence_id='evd_legacy'"
                )
            ).one()
        assert record.record_kind == "original"
        assert record.storage_provider == "legacy"
        assert record.integrity_status == "unverified"
        assert record.storage_key is None
        assert custody.action == "legacy_registered"
        assert custody.sequence_no == 1
        assert custody.previous_event_hash is None
        assert len(custody.event_hash) == 64
    finally:
        engine.dispose()

    database = Database(database_url)
    try:
        verification = EvidenceVault.local(database, tmp_path / "objects").verify_custody_chain(
            "tnt_a", "evd_legacy"
        )
        assert verification.valid is True
        assert verification.event_count == 1
    finally:
        database.dispose()


def test_connector_migration_upgrades_populated_component4_database(tmp_path, monkeypatch):
    database_path = tmp_path / "connector-upgrade.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ASSURANCEOS_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "0004_evidence_vault")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(tenant_id, slug, name, status, region, created_at, updated_at) "
                    "VALUES ('tnt_connector', 'connector', 'Connector Tenant', 'active', NULL, "
                    "'2026-08-06 18:00:00', '2026-08-06 18:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO evidence_records ("
                    "evidence_id, tenant_id, engagement_id, task_id, acquisition_key, record_kind, "
                    "source_type, source_locator, content_sha256, storage_provider, storage_key, "
                    "object_uri, original_filename, mime_type, content_encoding, size_bytes, "
                    "classification, source_time, collected_at, accepted, tainted, integrity_status, "
                    "last_verified_at, retention_until, legal_hold, deleted_at, deletion_reason, metadata_json"
                    ") VALUES ("
                    "'evd_existing', 'tnt_connector', NULL, NULL, 'existing', 'original', "
                    "'upload', 'upload://existing', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'local', 'objects/aa', 'file:///objects/aa', 'existing.json', "
                    "'application/json', NULL, 2, 'internal', NULL, '2026-08-06 18:01:00', "
                    "1, 0, 'verified', '2026-08-06 18:01:00', NULL, 0, NULL, NULL, '{}'"
                    ")"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {
            "connector_instances",
            "collection_grants",
            "connector_checkpoints",
            "connector_runs",
            "collected_source_objects",
        }.issubset(tables)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM evidence_records WHERE evidence_id='evd_existing'")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == current_head()
    finally:
        engine.dispose()
