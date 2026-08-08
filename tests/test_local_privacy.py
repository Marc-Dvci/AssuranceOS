from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from assuranceos.config import Settings
from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.vault import (
    Ed25519ManifestSigner,
    EvidenceVault,
    ExportPackageError,
    import_signed_bundle,
)


ROOT = Path(__file__).resolve().parents[1]


def _privacy_environment(monkeypatch, tmp_path: Path) -> None:
    export_key = tmp_path / "export.pem"
    execution_key = tmp_path / "execution.pem"
    export_key.write_text("mounted-private-key", encoding="utf-8")
    execution_key.write_text("mounted-private-key", encoding="utf-8")
    values = {
        "ASSURANCEOS_ENV": "local-privacy",
        "ASSURANCEOS_DATABASE_URL": "postgresql+psycopg://user:secret@postgres/assuranceos",
        "ASSURANCEOS_AUTO_CREATE_SCHEMA": "false",
        "ASSURANCEOS_MODEL_MODE": "local",
        "ASSURANCEOS_LOCAL_PRIVACY_MODE": "true",
        "ASSURANCEOS_LOCAL_MODEL_URL": "http://model:8080/v1",
        "ASSURANCEOS_LOCAL_MODEL_ALLOWED_HOSTS": "model",
        "ASSURANCEOS_EVIDENCE_STORAGE": "local",
        "ASSURANCEOS_AUTH_MODE": "jwt",
        "ASSURANCEOS_AUTH_JWT_ISSUER": "urn:assuranceos:local-privacy",
        "ASSURANCEOS_AUTH_JWT_AUDIENCE": "assuranceos-local",
        "ASSURANCEOS_AUTH_JWT_SECRET": "a-local-private-secret-with-32-bytes",
        "ASSURANCEOS_AUTH_JWT_ALGORITHMS": "HS256",
        "ASSURANCEOS_CONTROL_TEST_PUBLIC_KEY": str(
            ROOT / "security/release-keys/control-test-release-public.pem"
        ),
        "ASSURANCEOS_AUDIT_PACK_PUBLIC_KEY": str(
            ROOT / "security/release-keys/audit-pack-release-public.pem"
        ),
        "ASSURANCEOS_EXPORT_SIGNING_PRIVATE_KEY": str(export_key),
        "ASSURANCEOS_EXECUTION_SIGNING_PRIVATE_KEY": str(execution_key),
        "ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_local_privacy_configuration_is_explicit_and_fail_closed(monkeypatch, tmp_path):
    _privacy_environment(monkeypatch, tmp_path)
    settings = Settings.from_env()
    assert settings.is_local_privacy is True
    assert settings.model_mode == "local"
    assert settings.local_model_allowed_hosts == ("model",)

    monkeypatch.setenv("ASSURANCEOS_LOCAL_MODEL_URL", "https://hosted-model.example/v1")
    with pytest.raises(ValueError, match="outside ASSURANCEOS_LOCAL_MODEL_ALLOWED_HOSTS"):
        Settings.from_env()


def test_local_privacy_rejects_hosted_fallback_and_unprotected_state(monkeypatch, tmp_path):
    _privacy_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("ASSURANCEOS_MODEL_MODE", "vertex")
    with pytest.raises(ValueError, match="MODEL_MODE=local"):
        Settings.from_env()

    _privacy_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("ASSURANCEOS_AUTH_MODE", "disabled")
    with pytest.raises(ValueError, match="requires JWT authentication"):
        Settings.from_env()


def test_signed_bundle_is_verified_then_admitted_as_one_sealed_object(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "private.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt_source", slug="source", name="Source"))
        TenantRepository(session).add(Tenant(tenant_id="tnt_private", slug="private", name="Private"))
    signer = Ed25519ManifestSigner(
        private_key=Ed25519PrivateKey.generate(), key_id="source-export-v1"
    )
    vault = EvidenceVault.local(database, tmp_path / "objects", export_signer=signer)
    try:
        original = vault.ingest_bytes(
            tenant_id="tnt_source",
            payload=b'{"control":"effective"}',
            source_type="canonical-source",
            source_locator="source://control/1",
            actor_id="source-connector",
            accepted=True,
        )
        package = tmp_path / "bundle.zip"
        vault.create_export(
            tenant_id="tnt_source",
            evidence_ids=[original.evidence_id],
            destination=package,
            actor_id="source-auditor",
            purpose="private transfer",
        )
        keys = {"source-export-v1": signer.public_key_pem()}
        admitted, verification = import_signed_bundle(
            vault,
            package=package,
            tenant_id="tnt_private",
            actor_id="private-auditor",
            trusted_public_keys=keys,
        )
        replay, _ = import_signed_bundle(
            vault,
            package=package,
            tenant_id="tnt_private",
            actor_id="private-auditor",
            trusted_public_keys=keys,
        )
        assert verification.valid and verification.signature_valid is True
        assert admitted.evidence_id == replay.evidence_id
        assert admitted.source_type == "assuranceos-evidence-bundle"
        assert admitted.metadata["sealed_bundle"] is True
        assert admitted.metadata["package_sha256"] == verification.package_sha256
        assert vault.read_bytes(
            "tnt_private",
            admitted.evidence_id,
            actor_id="private-auditor",
            purpose="verify sealed transfer",
        ) == package.read_bytes()

        with pytest.raises(ExportPackageError, match="admission refused"):
            import_signed_bundle(
                vault,
                package=package,
                tenant_id="tnt_private",
                actor_id="private-auditor",
                trusted_public_keys={"different-key": signer.public_key_pem()},
            )
    finally:
        database.dispose()
