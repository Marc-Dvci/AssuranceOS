from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from assuranceos.db.models import EvidenceCustodyEvent, EvidenceRecord, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.vault import (
    AcquisitionConflictError,
    EvidenceDeletedError,
    EvidenceNotFoundError,
    EvidenceVault,
    ImmutableObjectConflictError,
    LocalObjectStore,
    RetentionPolicyError,
)


@pytest.fixture
def database(tmp_path: Path):
    db = Database.from_sqlite_path(tmp_path / "vault.db")
    db.create_schema()
    with db.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_a", slug="a", name="Tenant A"))
        TenantRepository(session).add(Tenant(tenant_id="tnt_b", slug="b", name="Tenant B"))
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def vault(database: Database, tmp_path: Path) -> EvidenceVault:
    return EvidenceVault.local(database, tmp_path / "objects")


def ingest(vault: EvidenceVault, **overrides):
    values = {
        "tenant_id": "tnt_a",
        "payload": b'{"change":"approved"}',
        "source_type": "github",
        "source_locator": "github://asteria/payments/pull/42",
        "actor_id": "connector:github",
        "actor_type": "service",
        "acquisition_key": "github:asteria/payments:pull:42:v1",
        "original_filename": "pull-42.json",
        "mime_type": "application/json",
        "accepted": True,
    }
    values.update(overrides)
    return vault.ingest_bytes(**values)


def test_ingestion_preserves_acquisition_identity_while_deduplicating_bytes(
    vault: EvidenceVault,
):
    first = ingest(vault)
    retry = ingest(vault)
    second_source = ingest(
        vault,
        source_locator="jira://CHANGE-42",
        acquisition_key="jira:CHANGE-42:v1",
    )
    other_tenant = ingest(
        vault,
        tenant_id="tnt_b",
        source_locator="github://other/repo/pull/42",
        acquisition_key="github:other/repo:pull:42:v1",
    )

    assert retry.evidence_id == first.evidence_id
    assert second_source.evidence_id != first.evidence_id
    assert second_source.storage_key == first.storage_key
    assert other_tenant.storage_key == first.storage_key

    store = vault.object_store
    assert len(store.iter_objects("tnt_a")) == 1
    assert len(store.iter_objects("tnt_b")) == 1


def test_acquisition_key_rejects_different_bytes(vault: EvidenceVault):
    ingest(vault)
    with pytest.raises(AcquisitionConflictError):
        ingest(vault, payload=b"different")


def test_custody_chain_is_append_only_and_detects_database_tampering(
    vault: EvidenceVault, database: Database
):
    item = ingest(vault)
    assert vault.read_bytes(
        "tnt_a", item.evidence_id, actor_id="auditor@example.test", purpose="fieldwork"
    ) == b'{"change":"approved"}'
    vault.set_retention(
        "tnt_a",
        item.evidence_id,
        actor_id="records-owner@example.test",
        retention_until=date(2027, 8, 6),
        legal_hold=False,
        reason="annual retention review",
    )

    valid = vault.verify_custody_chain("tnt_a", item.evidence_id)
    assert valid.valid is True
    assert valid.event_count == 3

    with database.transaction() as session:
        session.execute(
            update(EvidenceCustodyEvent)
            .where(
                EvidenceCustodyEvent.evidence_id == item.evidence_id,
                EvidenceCustodyEvent.sequence_no == 2,
            )
            .values(details_json={"purpose": "altered"})
        )

    invalid = vault.verify_custody_chain("tnt_a", item.evidence_id)
    assert invalid.valid is False
    assert "hash mismatch" in (invalid.error or "")


def test_integrity_verification_fails_closed_after_object_tampering(
    vault: EvidenceVault, database: Database
):
    item = ingest(vault)
    assert item.storage_key is not None
    store = vault.object_store
    assert isinstance(store, LocalObjectStore)
    object_path = store._path("tnt_a", item.storage_key)
    object_path.chmod(0o600)
    object_path.write_bytes(b"tampered")

    with pytest.raises(ImmutableObjectConflictError):
        vault.verify_integrity("tnt_a", item.evidence_id)

    with database.read_session() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.evidence_id == item.evidence_id)
        )
        assert record is not None
        assert record.integrity_status == "mismatch"
        assert record.last_verified_at is not None


def test_derivative_records_explicit_lineage_and_inherits_protection(vault: EvidenceVault):
    source = ingest(vault, tainted=True, legal_hold=True)
    derivative = vault.create_derivative(
        tenant_id="tnt_a",
        source_evidence_ids=[source.evidence_id],
        payload=b'{"change":"[REDACTED]"}',
        operation="redaction",
        tool_version="redactor@1.2.0",
        actor_id="evidence-custodian",
        original_filename="pull-42.redacted.json",
        mime_type="application/json",
        parameters={"fields": ["change"]},
    )

    assert derivative.record_kind == "derived"
    assert derivative.tainted is True
    assert derivative.legal_hold is True
    graph = vault.lineage("tnt_a", derivative.evidence_id)
    assert {node.evidence_id for node in graph.nodes} == {
        source.evidence_id,
        derivative.evidence_id,
    }
    assert len(graph.edges) == 1
    assert graph.edges[0].operation == "redaction"


def test_tenant_scope_prevents_cross_tenant_reads(vault: EvidenceVault):
    item = ingest(vault)
    with pytest.raises(EvidenceNotFoundError):
        vault.get("tnt_b", item.evidence_id)
    with pytest.raises(EvidenceNotFoundError):
        vault.read_bytes(
            "tnt_b", item.evidence_id, actor_id="other", purpose="unauthorized"
        )


def test_retention_tombstone_and_garbage_collection_respect_shared_objects(
    vault: EvidenceVault,
):
    first = ingest(vault, retention_until=date(2026, 8, 1))
    second = ingest(
        vault,
        source_locator="jira://CHANGE-42",
        acquisition_key="jira:CHANGE-42:v1",
        retention_until=date(2026, 8, 1),
    )
    assert first.storage_key == second.storage_key

    vault.purge(
        "tnt_a",
        first.evidence_id,
        actor_id="records-owner",
        reason="retention expired",
        as_of=date(2026, 8, 6),
    )
    with pytest.raises(EvidenceDeletedError):
        vault.read_bytes("tnt_a", first.evidence_id, actor_id="auditor", purpose="test")
    retained = vault.collect_garbage("tnt_a", grace_period=timedelta(0))
    assert retained.deleted == 0

    vault.purge(
        "tnt_a",
        second.evidence_id,
        actor_id="records-owner",
        reason="retention expired",
        as_of=date(2026, 8, 6),
    )
    collected = vault.collect_garbage("tnt_a", grace_period=timedelta(0))
    assert collected.deleted == 1
    assert vault.object_store.iter_objects("tnt_a") == []


def test_legal_hold_and_unexpired_retention_block_tombstone(vault: EvidenceVault):
    held = ingest(vault, legal_hold=True, retention_until=date(2026, 8, 1))
    with pytest.raises(RetentionPolicyError, match="legal hold"):
        vault.purge(
            "tnt_a",
            held.evidence_id,
            actor_id="records-owner",
            reason="request",
            as_of=date(2026, 8, 6),
        )

    future = ingest(
        vault,
        source_locator="jira://CHANGE-99",
        acquisition_key="jira:CHANGE-99:v1",
        retention_until=date(2027, 1, 1),
    )
    with pytest.raises(RetentionPolicyError, match="retained until"):
        vault.purge(
            "tnt_a",
            future.evidence_id,
            actor_id="records-owner",
            reason="request",
            as_of=date(2026, 8, 6),
        )


def test_export_is_self_contained_verifiable_and_includes_ancestors(
    vault: EvidenceVault, tmp_path: Path
):
    source = ingest(vault)
    derivative = vault.create_derivative(
        tenant_id="tnt_a",
        source_evidence_ids=[source.evidence_id],
        payload=b'{"change":"[REDACTED]"}',
        operation="redaction",
        tool_version="redactor@1.2.0",
        actor_id="evidence-custodian",
        mime_type="application/json",
    )
    package = tmp_path / "evidence-export.zip"
    result = vault.create_export(
        tenant_id="tnt_a",
        evidence_ids=[derivative.evidence_id],
        destination=package,
        actor_id="auditor@example.test",
        purpose="external audit support",
    )

    assert result.valid is True
    assert result.evidence_count == 2
    assert result.object_count == 2
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["requested_evidence_ids"] == [derivative.evidence_id]
    assert {item["evidence_id"] for item in manifest["evidence"]} == {
        source.evidence_id,
        derivative.evidence_id,
    }
    assert len(manifest["lineage"]) == 1

    tampered = tmp_path / "tampered-export.zip"
    with zipfile.ZipFile(package) as source_archive, zipfile.ZipFile(tampered, "w") as target:
        for name in source_archive.namelist():
            payload = source_archive.read(name)
            if name.startswith("objects/"):
                payload += b"tampered"
                target.writestr(name, payload)
                for remaining in source_archive.namelist()[source_archive.namelist().index(name) + 1 :]:
                    target.writestr(remaining, source_archive.read(remaining))
                break
            target.writestr(name, payload)
    invalid = vault.verify_export(tampered)
    assert invalid.valid is False
    assert any("checksum mismatch" in error or "size mismatch" in error for error in invalid.errors)


def test_derivative_requires_explicit_classification_for_mixed_sources(vault: EvidenceVault):
    internal = ingest(vault)
    restricted = ingest(
        vault,
        source_locator="jira://CHANGE-42",
        acquisition_key="jira:CHANGE-42:v1",
        classification="restricted",
    )
    with pytest.raises(ValueError, match="classification must be supplied"):
        vault.create_derivative(
            tenant_id="tnt_a",
            source_evidence_ids=[internal.evidence_id, restricted.evidence_id],
            payload=b"combined",
            operation="merge",
            tool_version="normalizer@1",
            actor_id="evidence-custodian",
        )

    combined = vault.create_derivative(
        tenant_id="tnt_a",
        source_evidence_ids=[internal.evidence_id, restricted.evidence_id],
        payload=b"combined",
        operation="merge",
        tool_version="normalizer@1",
        actor_id="evidence-custodian",
        classification="restricted",
    )
    assert combined.classification == "restricted"


def test_unknown_engagement_is_rejected_before_object_storage(vault: EvidenceVault):
    with pytest.raises(EvidenceNotFoundError, match="engagement not found"):
        ingest(vault, engagement_id="eng_missing")
    assert vault.object_store.iter_objects("tnt_a") == []


def test_export_verifier_recomputes_embedded_custody_chain(vault: EvidenceVault, tmp_path: Path):
    item = ingest(vault)
    package = tmp_path / "valid.zip"
    vault.create_export(
        tenant_id="tnt_a",
        evidence_ids=[item.evidence_id],
        destination=package,
        actor_id="auditor",
        purpose="custody verification test",
    )

    tampered = tmp_path / "custody-tampered.zip"
    with zipfile.ZipFile(package) as source_archive:
        manifest = json.loads(source_archive.read("manifest.json"))
        manifest["evidence"][0]["custody"][0]["actor_id"] = "attacker"
        manifest_bytes = json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        manifest_sha = __import__("hashlib").sha256(manifest_bytes).hexdigest()
        with zipfile.ZipFile(tampered, "w") as target:
            target.writestr("manifest.json", manifest_bytes)
            target.writestr("manifest.sha256", f"{manifest_sha}  manifest.json\n")
            for name in source_archive.namelist():
                if name.startswith("objects/"):
                    target.writestr(name, source_archive.read(name))

    verification = vault.verify_export(tampered)
    assert verification.valid is False
    assert any("custody event hash mismatch" in error for error in verification.errors)


def test_the_object_store_refuses_a_key_that_is_not_content_addressed(tmp_path: Path):
    """Both path components are matched against a fixed shape, not screened for traversal.

    A store that accepted any key without ``..`` in it was permitting a degree of
    freedom nothing ever used: every key it holds comes from ``key_for_digest``.
    Each of these is refused before anything is joined to a path, so the refusal
    does not depend on the containment check behind it noticing afterwards.
    """

    store = LocalObjectStore(tmp_path / "objects")
    refused = [
        "../../etc/passwd",
        "/etc/passwd",
        "objects/../../escape",
        "objects/ab/cd/not-a-digest",
        "objects/AB/CD/" + "a" * 64,
        "objects/ab/cd/" + "a" * 63,
        "arbitrary/path",
        "",
    ]
    for key in refused:
        with pytest.raises(ValueError, match="content-addressed"):
            store.open("tnt_example", key)

    # And the shape it does produce is accepted, so the rule is not simply
    # refusing everything.
    digest = "a" * 64
    assert store.key_for_digest(digest) == f"objects/aa/aa/{digest}"
    with pytest.raises(Exception) as excinfo:
        store.open("tnt_example", store.key_for_digest(digest))
    assert "not a content-addressed" not in str(excinfo.value)


def test_the_object_store_refuses_a_tenant_that_is_not_one_safe_segment(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    key = store.key_for_digest("b" * 64)
    for tenant in ("../elsewhere", "/absolute", "tenant/with/slashes", ".hidden", ""):
        with pytest.raises(ValueError, match="safe storage segment"):
            store.open(tenant, key)
